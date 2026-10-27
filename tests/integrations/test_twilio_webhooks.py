from dataclasses import dataclass, field

import orjson
import pytest
from pydantic import SecretStr
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from twilio.request_validator import RequestValidator

from voxos.credentials.models import SecretOwner, SecretPurpose, SecretReference
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.tenant import tenant_scoped_session_factory
from voxos.integrations.errors import IntegrationWebhookError
from voxos.integrations.models import (
    IntegrationActionResponse,
    IntegrationAuthType,
    IntegrationCallStatus,
    IntegrationCapability,
    IntegrationCredentialKind,
    IntegrationCredentialStatus,
    IntegrationDirection,
    IntegrationProviderType,
    IntegrationResourceStatus,
    IntegrationResourceType,
    IntegrationRouteStatus,
    IntegrationStatus,
    IntegrationWebhookPurpose,
    TwilioPhoneNumberCandidate,
)
from voxos.integrations.provider_registry import IntegrationProviderRegistry
from voxos.integrations.providers.base import (
    IntegrationCredentialValidation,
    IntegrationProviderDefinition,
    TwilioCredentialBundle,
)
from voxos.integrations.providers.twilio.constants import TWILIO_SIGNATURE_HEADER_NAME
from voxos.integrations.providers.twilio.media_streams import (
    TwilioAudioBridgeAdapter,
    TwilioAudioBridgeSession,
    TwilioMediaStreamBridge,
)
from voxos.integrations.records import (
    IntegrationCallSessionRecord,
    IntegrationCredentialRecord,
    IntegrationMessageRecord,
    IntegrationResourceRecord,
    IntegrationWebhookRouteRecord,
    UserIntegrationRecord,
)
from voxos.integrations.services import (
    IntegrationWebhookRouteContext,
    UserIntegrationService,
    outbound_call_callback_url,
    route_key_hash,
)
from voxos.integrations.webhooks import TwilioWebhookProcessor, twilio_validator_params
from voxos.voice.errors import MissingLiveKitCredentialsError
from voxos.whatsapp.mobile_registration import (
    WhatsAppMobileRegistrationService,
)
from voxos.whatsapp.models import WhatsAppMobileRegistrationChannel
from voxos.whatsapp.service import WhatsAppIntegrationService
from voxos.whatsapp.settings import WhatsAppWorkerSettings

ACCOUNT_SID = "AC" + "0" * 32
API_KEY_SID = "SK" + "1" * 32  # pragma: allowlist secret
PHONE_NUMBER = "+15551234567"
SEEDED_RESOURCE_MISSING = "expected seeded resource"
WEBSOCKET_MESSAGE_MISSING = "expected websocket message"
UNUSED_PROVIDER_ERROR = "test Twilio provider should not be called"
BRIDGE_CONNECTION_FAILED_ERROR = "bridge connection failed"


@dataclass(frozen=True, slots=True)
class UnusedTwilioProvider:
    def definition(self) -> IntegrationProviderDefinition:
        raise AssertionError(UNUSED_PROVIDER_ERROR)

    async def validate_credentials(
        self,
        credentials: TwilioCredentialBundle,
    ) -> IntegrationCredentialValidation:
        raise AssertionError(UNUSED_PROVIDER_ERROR)

    async def list_phone_numbers(
        self,
        credentials: TwilioCredentialBundle,
    ) -> tuple[TwilioPhoneNumberCandidate, ...]:
        raise AssertionError(UNUSED_PROVIDER_ERROR)

    async def list_resources(
        self,
        credentials: TwilioCredentialBundle,
    ) -> tuple[TwilioPhoneNumberCandidate, ...]:
        raise AssertionError(UNUSED_PROVIDER_ERROR)

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
        raise AssertionError(UNUSED_PROVIDER_ERROR)

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
        raise AssertionError(UNUSED_PROVIDER_ERROR)

    async def send_sms(
        self,
        *,
        credentials: TwilioCredentialBundle,
        from_number: str,
        to_number: str,
        body: str,
        status_callback_url: str | None,
    ) -> IntegrationActionResponse:
        raise AssertionError(UNUSED_PROVIDER_ERROR)

    async def send_message(
        self,
        *,
        credentials: TwilioCredentialBundle,
        from_number: str,
        to_number: str,
        body: str,
        status_callback_url: str | None,
    ) -> IntegrationActionResponse:
        raise AssertionError(UNUSED_PROVIDER_ERROR)

    async def start_outbound_call(
        self,
        *,
        credentials: TwilioCredentialBundle,
        from_number: str,
        to_number: str,
        twiml_url: str,
        status_callback_url: str | None,
    ) -> IntegrationActionResponse:
        raise AssertionError(UNUSED_PROVIDER_ERROR)


def unused_provider_registry() -> IntegrationProviderRegistry:
    return IntegrationProviderRegistry(twilio=UnusedTwilioProvider())


@pytest.mark.asyncio
async def test_inbound_sms_webhook_validates_signature_and_stores_full_body(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    route_key = "route-key"
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        route = await seed_twilio_integration(
            session=session,
            secret_vault=user_secret_vault,
            route_key=route_key,
        )
        params = {
            "AccountSid": ACCOUNT_SID,
            "MessageSid": "SM" + "9" * 32,
            "From": "+15550000000",
            "To": PHONE_NUMBER,
            "Body": "retain this body",
        }
        signature = RequestValidator("auth-token").compute_signature(
            "https://api.example.com/api/integrations/webhooks/twilio/sms/route-key",
            params,
        )
        processor = TwilioWebhookProcessor(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url="https://api.example.com",
            provider_registry=unused_provider_registry(),
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
        )

        await processor.handle_sms(
            route_context=IntegrationWebhookRouteContext(
                route=route,
                route_key_hash=route_key_hash(route_key),
            ),
            params=params,
            signature=signature,
        )
        message = (
            await session.exec(
                select(IntegrationMessageRecord).where(
                    col(IntegrationMessageRecord.external_message_id) == params["MessageSid"],
                ),
            )
        ).one()

    assert message.body == "retain this body"


@pytest.mark.asyncio
async def test_inbound_sms_verification_code_is_captured_without_message_record(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    route_key = "route-key"
    scoped_factory = tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-1",
    )
    async with scoped_factory() as session:
        route = await seed_twilio_integration(
            session=session,
            secret_vault=user_secret_vault,
            route_key=route_key,
        )
        if route.resource_id is None:
            raise AssertionError(SEEDED_RESOURCE_MISSING)
        registration = await WhatsAppMobileRegistrationService(
            session=session,
            secret_vault=user_secret_vault,
        ).start(
            user_id="user-1",
            source_resource_id=route.resource_id,
            channel=WhatsAppMobileRegistrationChannel.SMS,
            integration_service=WhatsAppIntegrationService(
                session=session,
                session_factory=scoped_factory,
                secret_vault=user_secret_vault,
                settings=WhatsAppWorkerSettings(),
            ),
        )
        params = {
            "AccountSid": ACCOUNT_SID,
            "MessageSid": "SM" + "8" * 32,
            "From": "+15550000000",
            "To": PHONE_NUMBER,
            "Body": "Your WhatsApp Business code is 123-456",
        }
        signature = RequestValidator("auth-token").compute_signature(
            "https://api.example.com/api/integrations/webhooks/twilio/sms/route-key",
            params,
        )
        await TwilioWebhookProcessor(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url="https://api.example.com",
            provider_registry=unused_provider_registry(),
            session_factory=scoped_factory,
        ).handle_sms(
            route_context=IntegrationWebhookRouteContext(
                route=route,
                route_key_hash=route_key_hash(route_key),
            ),
            params=params,
            signature=signature,
        )
        captured = await WhatsAppMobileRegistrationService(
            session=session,
            secret_vault=user_secret_vault,
        ).get_status(
            user_id="user-1",
            registration_id=registration.registration_id,
        )
        messages = (await session.exec(select(IntegrationMessageRecord))).all()

    assert messages == []
    assert captured.state == "code_received"
    assert captured.candidates[0].code == "123456"


@pytest.mark.asyncio
async def test_twilio_voice_missing_livekit_fails_before_stream_response(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    route_key = "voice-route-key"
    voice_url = "https://api.example.com/api/integrations/webhooks/twilio/voice/voice-route-key"

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
        seeded_route = await seed_twilio_integration(
            session=session,
            secret_vault=user_secret_vault,
            route_key="sms-route-key",
        )
        resource = await session.get(IntegrationResourceRecord, seeded_route.resource_id)
        if resource is None:
            raise AssertionError(SEEDED_RESOURCE_MISSING)
        resource.retained_metadata_json = {
            **resource.retained_metadata_json,
            "current_webhooks": {
                **resource.retained_metadata_json["current_webhooks"],
                "voice_url": voice_url,
                "media_stream_url": "wss://api.example.com/media/voice-route-key",
            },
        }
        route = IntegrationWebhookRouteRecord(
            user_id="user-1",
            integration_id=seeded_route.integration_id,
            resource_id=resource.id,
            provider=IntegrationProviderType.TWILIO.value,
            purpose=IntegrationWebhookPurpose.VOICE.value,
            route_key_hash=route_key_hash(route_key),
            status=IntegrationRouteStatus.ACTIVE.value,
        )
        session.add(route)
        await session.commit()
        await session.refresh(route)
        params = {
            "AccountSid": ACCOUNT_SID,
            "CallSid": "CA" + "6" * 32,
            "From": "+15550000000",
            "To": PHONE_NUMBER,
        }
        signature = RequestValidator("auth-token").compute_signature(voice_url, params)
        processor = TwilioWebhookProcessor(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url="https://api.example.com",
            provider_registry=unused_provider_registry(),
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            voice_worker_demand_requester=missing_livekit_requester,
        )

        with pytest.raises(MissingLiveKitCredentialsError):
            await processor.handle_voice(
                route_context=IntegrationWebhookRouteContext(
                    route=route,
                    route_key_hash=route_key_hash(route_key),
                ),
                params=params,
                signature=signature,
            )
        await session.rollback()
        calls = (await session.exec(select(IntegrationCallSessionRecord))).all()

    assert calls == []


@pytest.mark.asyncio
async def test_outbound_voice_callback_binds_original_call_route(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    route_key = "voice-route-key"
    base_voice_url = f"https://api.example.com/api/integrations/webhooks/twilio/voice/{route_key}"

    async def request_worker(**_kwargs: object) -> None:
        return None

    async def wait_for_worker(**_kwargs: object) -> None:
        return None

    async with tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-1",
    )() as session:
        seeded_route = await seed_twilio_integration(
            session=session,
            secret_vault=user_secret_vault,
            route_key="sms-route-key",
        )
        resource = await session.get(
            IntegrationResourceRecord,
            seeded_route.resource_id,
        )
        if resource is None:
            raise AssertionError(SEEDED_RESOURCE_MISSING)
        resource.retained_metadata_json = {
            **resource.retained_metadata_json,
            "current_webhooks": {
                **resource.retained_metadata_json["current_webhooks"],
                "voice_url": base_voice_url,
                "media_stream_url": "wss://api.example.com/media/voice-route-key",
            },
        }
        route = IntegrationWebhookRouteRecord(
            user_id="user-1",
            integration_id=seeded_route.integration_id,
            resource_id=resource.id,
            provider=IntegrationProviderType.TWILIO.value,
            purpose=IntegrationWebhookPurpose.VOICE.value,
            route_key_hash=route_key_hash(route_key),
            status=IntegrationRouteStatus.ACTIVE.value,
        )
        reservation = IntegrationCallSessionRecord(
            user_id="user-1",
            integration_id=seeded_route.integration_id,
            resource_id=resource.id,
            provider=IntegrationProviderType.TWILIO.value,
            direction=IntegrationDirection.OUTBOUND.value,
            external_call_id="reservation-outbound-call",
            from_number=PHONE_NUMBER,
            to_number="+15557654321",
            status=IntegrationCallStatus.INITIATING.value,
            agent_id="outbound-agent",
            runtime_session_id="outbound-runtime",
            livekit_room_name="outbound-room",
        )
        session.add(route)
        session.add(reservation)
        await session.commit()

        params = {
            "AccountSid": ACCOUNT_SID,
            "CallSid": "CA" + "7" * 32,
            "Direction": "outbound-api",
            "From": PHONE_NUMBER,
            "To": "+15557654321",
        }
        callback_url = outbound_call_callback_url(
            base_voice_url,
            reservation.id,
        )
        signature = RequestValidator("auth-token").compute_signature(
            callback_url,
            params,
        )
        processor = TwilioWebhookProcessor(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url="https://api.example.com",
            provider_registry=unused_provider_registry(),
            session_factory=tenant_scoped_session_factory(
                sqlite_session_factory,
                "user-1",
            ),
            voice_worker_demand_requester=request_worker,
            voice_worker_readiness_waiter=wait_for_worker,
        )

        await processor.handle_voice(
            route_context=IntegrationWebhookRouteContext(
                route=route,
                route_key_hash=route_key_hash(route_key),
            ),
            params=params,
            signature=signature,
            outbound_call_session_id=reservation.id,
        )
        calls = (await session.exec(select(IntegrationCallSessionRecord))).all()

    assert len(calls) == 1
    assert calls[0].external_call_id == params["CallSid"]
    assert calls[0].agent_id == "outbound-agent"
    assert calls[0].runtime_session_id == "outbound-runtime"
    assert calls[0].livekit_room_name == "outbound-room"


@pytest.mark.asyncio
async def test_twilio_status_tracks_answer_and_completes_voice_demand(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    completed_runtime_ids: list[str] = []

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
        seeded_route = await seed_twilio_integration(
            session=session,
            secret_vault=user_secret_vault,
            route_key="sms-route-key",
        )
        resource = await session.get(IntegrationResourceRecord, seeded_route.resource_id)
        if resource is None:
            raise AssertionError(SEEDED_RESOURCE_MISSING)
        route = IntegrationWebhookRouteRecord(
            user_id="user-1",
            integration_id=seeded_route.integration_id,
            resource_id=resource.id,
            provider=IntegrationProviderType.TWILIO.value,
            purpose=IntegrationWebhookPurpose.STATUS.value,
            route_key_hash=route_key_hash("status-key"),
            status=IntegrationRouteStatus.ACTIVE.value,
        )
        call = IntegrationCallSessionRecord(
            user_id="user-1",
            integration_id=seeded_route.integration_id,
            resource_id=resource.id,
            provider=IntegrationProviderType.TWILIO.value,
            direction=IntegrationDirection.OUTBOUND.value,
            external_call_id="CA" + "7" * 32,
            status=IntegrationCallStatus.INITIATING.value,
            agent_id="outbound-agent",
            runtime_session_id="outbound-runtime",
            livekit_room_name="outbound-room",
        )
        session.add(route)
        session.add(call)
        await session.commit()
        processor = TwilioWebhookProcessor(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url="https://api.example.com",
            provider_registry=unused_provider_registry(),
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            voice_worker_demand_completer=complete_worker,
        )
        status_url = "https://api.example.com/api/integrations/webhooks/twilio/status/status-key"
        for call_status in ("in-progress", "completed"):
            params = {
                "AccountSid": ACCOUNT_SID,
                "CallSid": call.external_call_id,
                "CallStatus": call_status,
            }
            await processor.handle_status(
                route_context=IntegrationWebhookRouteContext(
                    route=route,
                    route_key_hash=route_key_hash("status-key"),
                ),
                params=params,
                signature=RequestValidator("auth-token").compute_signature(
                    status_url,
                    params,
                ),
            )

    assert call.status == IntegrationCallStatus.COMPLETED.value
    assert call.answered_at is not None
    assert call.ended_at is not None
    assert completed_runtime_ids == ["outbound-runtime"]


@pytest.mark.asyncio
async def test_inbound_sms_webhook_preserves_repeated_form_values_for_signature(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    route_key = "route-key"
    expected_url = "https://api.example.com/api/integrations/webhooks/twilio/sms/route-key"
    form_params = (
        ("AccountSid", ACCOUNT_SID),
        ("MessageSid", "SM" + "8" * 32),
        ("From", "+15550000000"),
        ("To", PHONE_NUMBER),
        ("Body", "retain this body"),
        ("MediaUrl", "https://media.example.com/one"),
        ("MediaUrl", "https://media.example.com/two"),
    )
    signature = RequestValidator("auth-token").compute_signature(
        expected_url,
        twilio_validator_params(form_params),
    )
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        route = await seed_twilio_integration(
            session=session,
            secret_vault=user_secret_vault,
            route_key=route_key,
        )
        processor = TwilioWebhookProcessor(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url="https://api.example.com",
            provider_registry=unused_provider_registry(),
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
        )

        await processor.handle_sms(
            route_context=IntegrationWebhookRouteContext(
                route=route,
                route_key_hash=route_key_hash(route_key),
            ),
            params=dict(form_params),
            signature_params=form_params,
            signature=signature,
        )
        message = (
            await session.exec(
                select(IntegrationMessageRecord).where(
                    col(IntegrationMessageRecord.external_message_id) == "SM" + "8" * 32,
                ),
            )
        ).one()

    assert message.body == "retain this body"


@pytest.mark.asyncio
async def test_inbound_sms_webhook_rejects_inactive_integration(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    route_key = "route-key"
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        route = await seed_twilio_integration(
            session=session,
            secret_vault=user_secret_vault,
            route_key=route_key,
        )
        integration = await session.get(UserIntegrationRecord, route.integration_id)
        assert integration is not None
        integration.enabled = False
        integration.status = IntegrationStatus.FAILED.value
        await session.commit()
        params = {
            "AccountSid": ACCOUNT_SID,
            "MessageSid": "SM" + "9" * 32,
            "From": "+15550000000",
            "To": PHONE_NUMBER,
            "Body": "blocked",
        }
        signature = RequestValidator("auth-token").compute_signature(
            "https://api.example.com/api/integrations/webhooks/twilio/sms/route-key",
            params,
        )
        processor = TwilioWebhookProcessor(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url="https://api.example.com",
            provider_registry=unused_provider_registry(),
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
        )

        with pytest.raises(IntegrationWebhookError):
            await processor.handle_sms(
                route_context=IntegrationWebhookRouteContext(
                    route=route,
                    route_key_hash=route_key_hash(route_key),
                ),
                params=params,
                signature=signature,
            )
        messages = (await session.exec(select(IntegrationMessageRecord))).all()

    assert messages == []


@pytest.mark.asyncio
async def test_media_stream_websocket_validates_twilio_signature(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    route_key = "media-route-key"
    call_sid = "CA" + "8" * 32
    stream_sid = "MZ" + "7" * 32
    stream_url = "wss://api.example.com/api/integrations/streams/twilio/media/media-route-key"
    signature = RequestValidator("auth-token").compute_signature(stream_url, {})
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        route = await seed_twilio_media_stream_integration(
            session=session,
            secret_vault=user_secret_vault,
            route_key=route_key,
            call_sid=call_sid,
            stream_url=stream_url,
        )
        accepted_websocket = FakeWebSocket(
            headers={TWILIO_SIGNATURE_HEADER_NAME: signature},
            messages=[
                {"event": "connected"},
                {
                    "event": "start",
                    "start": {
                        "accountSid": ACCOUNT_SID,
                        "callSid": call_sid,
                        "streamSid": stream_sid,
                    },
                },
                {"event": "stop", "stop": {"streamSid": stream_sid}},
            ],
        )
        rejected_websocket = FakeWebSocket(headers={TWILIO_SIGNATURE_HEADER_NAME: "bad-signature"})
        service = UserIntegrationService(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url="https://api.example.com",
            provider_registry=unused_provider_registry(),
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
        )
        adapter = FakeAudioBridgeAdapter()
        bridge = TwilioMediaStreamBridge(
            service=service,
            audio_bridge_factory=lambda _user_id: immediate(adapter),
        )

        await bridge.handle_websocket(
            websocket=accepted_websocket,
            route_context=IntegrationWebhookRouteContext(
                route=route,
                route_key_hash=route_key_hash(route_key),
            ),
            signature=signature,
        )
        await bridge.handle_websocket(
            websocket=rejected_websocket,
            route_context=IntegrationWebhookRouteContext(
                route=route,
                route_key_hash=route_key_hash(route_key),
            ),
            signature="bad-signature",
        )
        call_session = (await session.exec(select(IntegrationCallSessionRecord))).one()

    assert accepted_websocket.accepted is True
    assert accepted_websocket.closed_codes == []
    assert rejected_websocket.accepted is False
    assert rejected_websocket.closed_codes == [1008]
    assert adapter.sessions[0].closed is True
    assert call_session.media_stream_id == stream_sid


@pytest.mark.asyncio
async def test_media_stream_bridge_start_failure_cancels_voice_demand(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    route_key = "media-route-key"
    call_sid = "CA" + "8" * 32
    stream_sid = "MZ" + "7" * 32
    stream_url = "wss://api.example.com/api/integrations/streams/twilio/media/media-route-key"
    signature = RequestValidator("auth-token").compute_signature(stream_url, {})
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
        route = await seed_twilio_media_stream_integration(
            session=session,
            secret_vault=user_secret_vault,
            route_key=route_key,
            call_sid=call_sid,
            stream_url=stream_url,
        )
        websocket = FakeWebSocket(
            headers={TWILIO_SIGNATURE_HEADER_NAME: signature},
            messages=[
                {
                    "event": "start",
                    "start": {
                        "accountSid": ACCOUNT_SID,
                        "callSid": call_sid,
                        "streamSid": stream_sid,
                    },
                },
            ],
        )
        service = UserIntegrationService(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url="https://api.example.com",
            provider_registry=unused_provider_registry(),
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            voice_worker_demand_canceller=cancel_worker,
        )
        bridge = TwilioMediaStreamBridge(
            service=service,
            audio_bridge_factory=lambda _user_id: immediate(FailingAudioBridgeAdapter()),
        )

        with pytest.raises(RuntimeError, match="bridge connection failed"):
            await bridge.handle_websocket(
                websocket=websocket,
                route_context=IntegrationWebhookRouteContext(
                    route=route,
                    route_key_hash=route_key_hash(route_key),
                ),
                signature=signature,
            )
        call_session = (await session.exec(select(IntegrationCallSessionRecord))).one()

    assert call_session.status == IntegrationCallStatus.FAILED.value
    assert call_session.ended_at is not None
    assert cancellations == [("runtime-session-1", "media_bridge_start_failed")]


async def seed_twilio_integration(
    *,
    session,
    secret_vault: UserSecretVaultService,
    route_key: str,
) -> IntegrationWebhookRouteRecord:
    integration = UserIntegrationRecord(
        user_id="user-1",
        provider=IntegrationProviderType.TWILIO.value,
        label="Twilio",
        status=IntegrationStatus.CONNECTED.value,
        enabled=True,
        auth_type=IntegrationAuthType.API_KEY.value,
        setup_state="connected",
        safe_metadata={"account_sid": ACCOUNT_SID, "api_key_sid": API_KEY_SID},
    )
    session.add(integration)
    await session.flush()
    credential = IntegrationCredentialRecord(
        user_id="user-1",
        integration_id=integration.id,
        provider=IntegrationProviderType.TWILIO.value,
        secret_kind=IntegrationCredentialKind.TWILIO_AUTH_TOKEN.value,
        status=IntegrationCredentialStatus.ACTIVE.value,
        public_identifier=ACCOUNT_SID,
        last_four="oken",
    )
    session.add(credential)
    await session.flush()
    api_key_credential = IntegrationCredentialRecord(
        user_id="user-1",
        integration_id=integration.id,
        provider=IntegrationProviderType.TWILIO.value,
        secret_kind=IntegrationCredentialKind.TWILIO_API_KEY_SECRET.value,
        status=IntegrationCredentialStatus.ACTIVE.value,
        public_identifier=API_KEY_SID,
        last_four="cret",
    )
    session.add(api_key_credential)
    await session.flush()
    await secret_vault.seal_secret(
        session=session,
        secret=SecretStr("auth-token"),
        reference=SecretReference(
            owner=SecretOwner(user_id="user-1"),
            provider=IntegrationProviderType.TWILIO.value,
            credential_id=credential.id,
            purpose=SecretPurpose.INTEGRATION_CREDENTIAL,
        ),
    )
    await secret_vault.seal_secret(
        session=session,
        secret=SecretStr("api-key-secret"),
        reference=SecretReference(
            owner=SecretOwner(user_id="user-1"),
            provider=IntegrationProviderType.TWILIO.value,
            credential_id=api_key_credential.id,
            purpose=SecretPurpose.INTEGRATION_CREDENTIAL,
        ),
    )
    resource = IntegrationResourceRecord(
        user_id="user-1",
        integration_id=integration.id,
        provider=IntegrationProviderType.TWILIO.value,
        resource_type=IntegrationResourceType.PHONE_NUMBER.value,
        external_id="PN" + "2" * 32,
        display_name=PHONE_NUMBER,
        status=IntegrationResourceStatus.CONNECTED.value,
        capabilities=[
            IntegrationCapability.SMS_READ.value,
            IntegrationCapability.SMS_SEND.value,
        ],
        retained_metadata_json={
            "current_webhooks": {
                "sms_url": "https://api.example.com/api/integrations/webhooks/twilio/sms/route-key",
                "status_callback": "https://api.example.com/api/integrations/webhooks/twilio/status/status-key",
            },
        },
    )
    session.add(resource)
    await session.flush()
    route = IntegrationWebhookRouteRecord(
        user_id="user-1",
        integration_id=integration.id,
        resource_id=resource.id,
        provider=IntegrationProviderType.TWILIO.value,
        purpose=IntegrationWebhookPurpose.SMS.value,
        route_key_hash=route_key_hash(route_key),
        status=IntegrationRouteStatus.ACTIVE.value,
    )
    session.add(route)
    await session.commit()
    await session.refresh(route)
    return route


async def seed_twilio_media_stream_integration(
    *,
    session,
    secret_vault: UserSecretVaultService,
    route_key: str,
    call_sid: str,
    stream_url: str,
) -> IntegrationWebhookRouteRecord:
    route = await seed_twilio_integration(
        session=session,
        secret_vault=secret_vault,
        route_key="sms-route-key",
    )
    resource = await session.get(IntegrationResourceRecord, route.resource_id)
    if resource is None:
        raise AssertionError(SEEDED_RESOURCE_MISSING)
    resource.capabilities = [
        IntegrationCapability.INBOUND_VOICE.value,
        IntegrationCapability.OUTBOUND_VOICE.value,
        IntegrationCapability.SMS_READ.value,
        IntegrationCapability.SMS_SEND.value,
    ]
    resource.retained_metadata_json = {
        **resource.retained_metadata_json,
        "current_webhooks": {
            **resource.retained_metadata_json["current_webhooks"],
            "media_stream_url": stream_url,
        },
    }
    call_session = IntegrationCallSessionRecord(
        user_id="user-1",
        integration_id=route.integration_id,
        resource_id=resource.id,
        provider=IntegrationProviderType.TWILIO.value,
        direction="inbound",
        external_call_id=call_sid,
        from_number="+15550000000",
        to_number=PHONE_NUMBER,
        status=IntegrationCallStatus.IN_PROGRESS.value,
        agent_id="agent-1",
        runtime_session_id="runtime-session-1",
        livekit_room_name="voxos-twilio-call",
        payload_json={},
    )
    session.add(call_session)
    media_route = IntegrationWebhookRouteRecord(
        user_id="user-1",
        integration_id=route.integration_id,
        resource_id=resource.id,
        provider=IntegrationProviderType.TWILIO.value,
        purpose=IntegrationWebhookPurpose.MEDIA_STREAM.value,
        route_key_hash=route_key_hash(route_key),
        status=IntegrationRouteStatus.ACTIVE.value,
    )
    session.add(media_route)
    await session.commit()
    await session.refresh(media_route)
    return media_route


@dataclass(slots=True)
class FakeWebSocket:
    headers: dict[str, str]
    messages: list[dict[str, object]] = field(default_factory=list)
    accepted: bool = False
    closed_codes: list[int] = field(default_factory=list)

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int) -> None:
        self.closed_codes.append(code)

    async def receive_text(self) -> str:
        if not self.messages:
            raise AssertionError(WEBSOCKET_MESSAGE_MISSING)
        return orjson.dumps(self.messages.pop(0)).decode()


@dataclass(slots=True)
class FakeAudioBridgeSession:
    closed: bool = False

    async def publish_twilio_payload(self, payload: str) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


@dataclass(slots=True)
class FakeAudioBridgeAdapter:
    sessions: list[FakeAudioBridgeSession] = field(default_factory=list)

    async def connect(
        self,
        *,
        websocket,
        call_session: IntegrationCallSessionRecord,
        user_id: str,
    ) -> TwilioAudioBridgeSession:
        session = FakeAudioBridgeSession()
        self.sessions.append(session)
        return session


@dataclass(slots=True)
class FailingAudioBridgeAdapter:
    async def connect(
        self,
        *,
        websocket,
        call_session: IntegrationCallSessionRecord,
        user_id: str,
    ) -> TwilioAudioBridgeSession:
        del websocket, call_session, user_id
        raise RuntimeError(BRIDGE_CONNECTION_FAILED_ERROR)


async def immediate(adapter: TwilioAudioBridgeAdapter) -> TwilioAudioBridgeAdapter:
    return adapter

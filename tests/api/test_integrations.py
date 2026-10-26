import logging
from dataclasses import dataclass

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.requests import Request

from voxos.api.integrations import (
    create_integrations_router,
    handle_communication_webhook,
    handle_telnyx_communication_webhook,
    integration_http_error,
)
from voxos.api.settings import ApiSettings
from voxos.auth.models import AuthenticatedUser
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.integrations.errors import IntegrationUpstreamError
from voxos.integrations.models import (
    IntegrationCapability,
    IntegrationProviderType,
    IntegrationWebhookPurpose,
    TwilioPhoneNumberCandidate,
)
from voxos.integrations.provider_registry import IntegrationProviderRegistry
from voxos.integrations.providers.base import (
    IntegrationCredentialValidation,
    TelnyxCredentialBundle,
    TelnyxPhoneNumberConfiguration,
    TelnyxPhoneNumberProvisioning,
    TwilioCredentialBundle,
)
from voxos.integrations.providers.telnyx.provider import TelnyxIntegrationProvider
from voxos.integrations.providers.twilio.provider import TwilioIntegrationProvider
from voxos.integrations.records import IntegrationWebhookRouteRecord
from voxos.integrations.services import (
    INTEGRATION_LABEL_EXISTS_ERROR,
    IntegrationWebhookRouteContext,
    IntegrationWebhookRouteLookupService,
    UserIntegrationService,
)
from voxos.integrations.telnyx_webhooks import TelnyxWebhookProcessor
from voxos.integrations.webhooks import TwilioWebhookProcessor
from voxos.voice.errors import InvalidLiveKitCredentialsError
from voxos.voice.livekit_tokens import LiveKitIdentityMetadataBuilder, LiveKitVoiceTokenService
from voxos.voice.settings import VoiceAgentSettings

ACCOUNT_SID = "AC" + "0" * 32
API_KEY_SID = "SK" + "1" * 32  # pragma: allowlist secret
PHONE_NUMBER_SID = "PN" + "2" * 32
PHONE_NUMBER = "+15551234567"
TELNYX_CONNECTION_ID = "1293384261075731499"
TELNYX_MESSAGING_PROFILE_ID = "182bd5e5-6e1a-4fe4-a799-aa6d9a6ab26e"
TELNYX_PUBLIC_KEY = "a" * 64
PROVIDER_DETAIL_ERROR = "provider detail"
DATABASE_DETAIL_MARKER = "db-detail-marker-7e6b"
PAYLOAD_MARKER = "payload-marker-5a4c"
SIGNATURE_MARKER = "signature-marker-3d2e"
ROUTE_KEY_MARKER = "route-key-marker-1b0a"


@dataclass(frozen=True, slots=True)
class StaticAuthDependency:
    authenticated_user: AuthenticatedUser

    async def __call__(self, request: Request) -> AuthenticatedUser:
        return self.authenticated_user


def authenticated_user(user_id: str = "user-1") -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user_id,
        firebase_user_id=f"firebase-{user_id}",
        email=f"{user_id}@example.com",
        email_verified=True,
    )


async def empty_json_request() -> dict[str, object]:
    return {"type": "http.request", "body": b"{}", "more_body": False}


def request_with_body(
    body: bytes,
    *,
    headers: list[tuple[bytes, bytes]],
) -> Request:
    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": headers,
            "query_string": b"",
        },
        receive=receive,
    )


def create_test_client(
    *,
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    public_base_url: str | None = "https://api.example.com",
    user_id: str = "user-1",
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_integrations_router(
            auth_dependency=StaticAuthDependency(authenticated_user(user_id)),
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            api_settings=ApiSettings(api_public_base_url=public_base_url),
            voice_settings=VoiceAgentSettings(),
            livekit_token_service=LiveKitVoiceTokenService(
                api_key=None,
                api_secret=None,
                metadata_builder=LiveKitIdentityMetadataBuilder(user_metadata_key="user_id"),
                ttl_seconds=60,
            ),
        ),
    )
    return TestClient(app)


def test_twilio_setup_response_excludes_plaintext_secrets(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_twilio_provider(monkeypatch)
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
    )

    response = client.post(
        "/api/integrations/twilio/setup",
        json={
            "label": "Twilio",
            "account_sid": ACCOUNT_SID,
            "api_key_sid": API_KEY_SID,
            "api_key_secret": "api-key-secret",  # pragma: allowlist secret
            "auth_token": "auth-token",
        },
    )

    assert response.status_code == 201
    assert "api-key-secret" not in response.text
    assert "auth-token" not in response.text
    body = response.json()
    assert body["integration"]["safe_metadata"]["account_sid"] == ACCOUNT_SID
    assert body["phone_numbers"][0]["phone_number"] == PHONE_NUMBER


def test_twilio_setup_duplicate_label_returns_client_error_before_provider_calls(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_calls = stub_twilio_provider(monkeypatch)
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
    )
    payload = {
        "label": "Twilio",
        "account_sid": ACCOUNT_SID,
        "api_key_sid": API_KEY_SID,
        "api_key_secret": "api-key-secret",  # pragma: allowlist secret
        "auth_token": "auth-token",
    }

    create_response = client.post("/api/integrations/twilio/setup", json=payload)
    calls_after_create = provider_calls.copy()
    duplicate_response = client.post("/api/integrations/twilio/setup", json=payload)

    assert create_response.status_code == 201
    assert duplicate_response.status_code == 422
    assert duplicate_response.json()["detail"] == INTEGRATION_LABEL_EXISTS_ERROR
    assert provider_calls == calls_after_create


def test_telnyx_setup_response_excludes_plaintext_secrets(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_telnyx_provider(monkeypatch)
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
    )

    response = client.post(
        "/api/integrations/telnyx/setup",
        json={
            "label": "Telnyx",
            "phone_number": PHONE_NUMBER,
            "api_key": "telnyx-secret",  # pragma: allowlist secret
            "webhook_public_key": TELNYX_PUBLIC_KEY,
            "confirm_number_assignment": True,
            "transcript_storage_enabled": False,
        },
    )

    assert response.status_code == 201
    assert "telnyx-secret" not in response.text
    body = response.json()
    assert body["integration"]["provider"] == "telnyx"
    assert body["integration"]["status"] == "connected"
    assert body["resources"][0]["display_name"] == PHONE_NUMBER
    assert body["resources"][0]["status"] == "connected"


def test_telnyx_readiness_repair_and_activity_are_tenant_scoped(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_telnyx_provider(monkeypatch)
    owner_client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
    )
    setup_response = owner_client.post(
        "/api/integrations/telnyx/setup",
        json={
            "label": "Telnyx",
            "phone_number": PHONE_NUMBER,
            "api_key": "telnyx-secret",  # pragma: allowlist secret
            "webhook_public_key": TELNYX_PUBLIC_KEY,
            "confirm_number_assignment": True,
            "transcript_storage_enabled": False,
        },
    )
    integration_id = setup_response.json()["integration"]["id"]

    readiness_response = owner_client.get(
        f"/api/integrations/{integration_id}/telnyx/readiness",
    )
    unconfirmed_repair = owner_client.post(
        f"/api/integrations/{integration_id}/telnyx/repair",
        json={"confirm_provider_changes": False},
    )
    repaired_response = owner_client.post(
        f"/api/integrations/{integration_id}/telnyx/repair",
        json={"confirm_provider_changes": True},
    )
    messages_response = owner_client.get(
        f"/api/integrations/{integration_id}/messages",
    )
    calls_response = owner_client.get(
        f"/api/integrations/{integration_id}/calls",
    )
    latency_response = owner_client.get(
        f"/api/integrations/{integration_id}/calls/latency?provider=telnyx",
    )

    assert readiness_response.status_code == 200
    readiness = readiness_response.json()
    assert readiness["provider"]["phone_number"] == PHONE_NUMBER
    assert readiness["provider"]["connection_assignment_matches"] is True
    assert "telnyx-secret" not in readiness_response.text
    assert "/communications/telnyx/" not in readiness_response.text
    assert unconfirmed_repair.status_code == 422
    assert repaired_response.status_code == 200
    assert repaired_response.json()["repaired"] is True
    assert messages_response.json() == {
        "messages": [],
        "offset": 0,
        "limit": 20,
        "next_offset": None,
    }
    assert calls_response.json() == {
        "calls": [],
        "offset": 0,
        "limit": 20,
        "next_offset": None,
    }
    assert latency_response.json() == {
        "integration_id": integration_id,
        "provider": "telnyx",
        "worker_registration_to_agent_ready": {
            "sample_count": 0,
            "p50_ms": None,
            "p95_ms": None,
        },
        "agent_ready_to_provider_dial": {
            "sample_count": 0,
            "p50_ms": None,
            "p95_ms": None,
        },
        "provider_dial_to_answer": {
            "sample_count": 0,
            "p50_ms": None,
            "p95_ms": None,
        },
        "provider_dial_to_media_ready": {
            "sample_count": 0,
            "p50_ms": None,
            "p95_ms": None,
        },
        "answer_to_first_speech": {
            "sample_count": 0,
            "p50_ms": None,
            "p95_ms": None,
        },
        "opening_scheduled_to_playout_complete": {
            "sample_count": 0,
            "p50_ms": None,
            "p95_ms": None,
        },
    }

    other_client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        user_id="user-2",
    )
    assert (
        other_client.get(
            f"/api/integrations/{integration_id}/telnyx/readiness",
        ).status_code
        == 404
    )
    assert other_client.get(f"/api/integrations/{integration_id}/messages").status_code == 404
    assert other_client.get(f"/api/integrations/{integration_id}/calls").status_code == 404
    assert (
        other_client.get(
            f"/api/integrations/{integration_id}/calls/latency",
        ).status_code
        == 404
    )


def test_api_public_base_url_requires_https() -> None:
    with pytest.raises(
        ValidationError,
        match="API_PUBLIC_BASE_URL must start with https://",
    ):
        ApiSettings(api_public_base_url="http://api.example.com")


def test_transient_integration_failure_maps_to_service_unavailable() -> None:
    error = integration_http_error(IntegrationUpstreamError("provider detail"))

    assert error.status_code == 503
    assert error.detail == "Integration provider is temporarily unavailable."


def test_phone_call_with_invalid_saved_livekit_settings_returns_service_unavailable(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_call_start(
        self: UserIntegrationService,
        **kwargs: object,
    ) -> object:
        del self, kwargs
        raise InvalidLiveKitCredentialsError

    monkeypatch.setattr(
        UserIntegrationService,
        "start_twilio_outbound_call",
        fail_call_start,
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
    )

    response = client.post(
        "/api/integrations/twilio/actions/call",
        json={
            "integration_id": "integration-1",
            "resource_id": "resource-1",
            "agent_id": "agent-1",
            "to_number": "+15557654321",
            "objective": "Introduce yourself and ask whether they need any help.",
        },
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "LiveKit voice routing is temporarily unavailable.",
    }


@pytest.mark.asyncio
async def test_telnyx_transient_webhook_failure_returns_service_unavailable(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_voice_processing(self: TelnyxWebhookProcessor, **kwargs: object) -> dict[str, object]:
        del self, kwargs
        raise IntegrationUpstreamError(PROVIDER_DETAIL_ERROR)

    monkeypatch.setattr(TelnyxWebhookProcessor, "handle_voice", fail_voice_processing)
    route = IntegrationWebhookRouteRecord(
        user_id="user-1",
        integration_id="integration-1",
        provider=IntegrationProviderType.TELNYX.value,
        purpose=IntegrationWebhookPurpose.VOICE.value,
        route_key_hash="a" * 64,
    )
    request = Request(
        {"type": "http", "method": "POST", "path": "/", "headers": []},
        receive=empty_json_request,
    )

    with pytest.raises(HTTPException) as error:
        await handle_telnyx_communication_webhook(
            provider_registry=IntegrationProviderRegistry(telnyx=TelnyxIntegrationProvider()),
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            public_base_url="https://api.example.com",
            purpose=IntegrationWebhookPurpose.VOICE,
            route_context=IntegrationWebhookRouteContext(
                route=route,
                route_key_hash=route.route_key_hash,
            ),
            request=request,
        )

    assert error.value.status_code == 503
    assert error.value.detail == "Integration provider is temporarily unavailable."


@pytest.mark.asyncio
async def test_telnyx_database_webhook_failure_is_safely_logged(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    database_failure_detail = DATABASE_DETAIL_MARKER

    async def fail_voice_processing(
        self: TelnyxWebhookProcessor,
        **kwargs: object,
    ) -> dict[str, object]:
        del self, kwargs
        raise SQLAlchemyError(database_failure_detail)

    monkeypatch.setattr(
        TelnyxWebhookProcessor,
        "handle_voice",
        fail_voice_processing,
    )
    route = webhook_route(provider=IntegrationProviderType.TELNYX)
    request = request_with_body(
        b'{"from":"payload-marker-5a4c"}',
        headers=[
            (b"telnyx-signature-ed25519", SIGNATURE_MARKER.encode()),
        ],
    )
    caplog.set_level(logging.ERROR, logger="voxos.api.integrations")

    with pytest.raises(HTTPException) as error:
        await handle_telnyx_communication_webhook(
            provider_registry=IntegrationProviderRegistry(
                telnyx=TelnyxIntegrationProvider(),
            ),
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            public_base_url="https://api.example.com",
            purpose=IntegrationWebhookPurpose.VOICE,
            route_context=IntegrationWebhookRouteContext(
                route=route,
                route_key_hash=route.route_key_hash,
            ),
            request=request,
        )

    assert error.value.status_code == 503
    assert error.value.detail == "Integrations are unavailable."
    assert_safe_database_failure_log(
        caplog,
        provider=IntegrationProviderType.TELNYX,
        route=route,
        sensitive_values=(
            DATABASE_DETAIL_MARKER,
            PAYLOAD_MARKER,
            SIGNATURE_MARKER,
            route.route_key_hash,
        ),
    )


@pytest.mark.asyncio
async def test_twilio_database_webhook_failure_is_safely_logged(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    route = webhook_route(provider=IntegrationProviderType.TWILIO)
    route_context = IntegrationWebhookRouteContext(
        route=route,
        route_key_hash=route.route_key_hash,
    )
    database_failure_detail = DATABASE_DETAIL_MARKER

    async def resolve_route(
        self: IntegrationWebhookRouteLookupService,
        **kwargs: object,
    ) -> IntegrationWebhookRouteContext:
        del self, kwargs
        return route_context

    async def fail_voice_processing(
        self: TwilioWebhookProcessor,
        **kwargs: object,
    ) -> str:
        del self, kwargs
        raise SQLAlchemyError(database_failure_detail)

    monkeypatch.setattr(
        IntegrationWebhookRouteLookupService,
        "resolve",
        resolve_route,
    )
    monkeypatch.setattr(
        TwilioWebhookProcessor,
        "handle_voice",
        fail_voice_processing,
    )
    request = request_with_body(
        b"From=payload-marker-5a4c",
        headers=[(b"x-twilio-signature", SIGNATURE_MARKER.encode())],
    )
    caplog.set_level(logging.ERROR, logger="voxos.api.integrations")

    with pytest.raises(HTTPException) as error:
        await handle_communication_webhook(
            provider_registry=IntegrationProviderRegistry(
                twilio=TwilioIntegrationProvider(),
            ),
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            api_settings=ApiSettings(
                api_public_base_url="https://api.example.com",
            ),
            provider=IntegrationProviderType.TWILIO,
            purpose=IntegrationWebhookPurpose.VOICE,
            route_key=ROUTE_KEY_MARKER,
            request=request,
        )

    assert error.value.status_code == 503
    assert error.value.detail == "Integrations are unavailable."
    assert_safe_database_failure_log(
        caplog,
        provider=IntegrationProviderType.TWILIO,
        route=route,
        sensitive_values=(
            DATABASE_DETAIL_MARKER,
            PAYLOAD_MARKER,
            SIGNATURE_MARKER,
            ROUTE_KEY_MARKER,
            route.route_key_hash,
        ),
    )


def test_list_and_delete_do_not_require_public_base_url(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        public_base_url=None,
    )

    list_response = client.get("/api/integrations")
    delete_response = client.delete("/api/integrations/missing-integration")

    assert list_response.status_code == 200
    assert list_response.json() == {"integrations": []}
    assert delete_response.status_code == 404


def stub_twilio_provider(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    provider_calls = {"validate": 0, "list": 0}

    async def validate_credentials(
        self: TwilioIntegrationProvider,
        credentials: TwilioCredentialBundle,
    ) -> IntegrationCredentialValidation:
        provider_calls["validate"] += 1
        return IntegrationCredentialValidation(
            valid=True,
            safe_metadata={
                "account_sid": credentials.account_sid,
                "api_key_sid": credentials.api_key_sid,
            },
        )

    async def list_phone_numbers(
        self: TwilioIntegrationProvider,
        credentials: TwilioCredentialBundle,
    ) -> tuple[TwilioPhoneNumberCandidate, ...]:
        provider_calls["list"] += 1
        return (
            TwilioPhoneNumberCandidate(
                sid=PHONE_NUMBER_SID,
                phone_number=PHONE_NUMBER,
                friendly_name="Support",
                capabilities=(
                    IntegrationCapability.INBOUND_VOICE,
                    IntegrationCapability.OUTBOUND_VOICE,
                    IntegrationCapability.SMS_READ,
                    IntegrationCapability.SMS_SEND,
                ),
            ),
        )

    monkeypatch.setattr(TwilioIntegrationProvider, "validate_credentials", validate_credentials)
    monkeypatch.setattr(TwilioIntegrationProvider, "list_phone_numbers", list_phone_numbers)
    return provider_calls


def webhook_route(
    *,
    provider: IntegrationProviderType,
) -> IntegrationWebhookRouteRecord:
    return IntegrationWebhookRouteRecord(
        user_id="user-1",
        integration_id="integration-1",
        resource_id="resource-1",
        provider=provider.value,
        purpose=IntegrationWebhookPurpose.VOICE.value,
        route_key_hash="a" * 64,
    )


def assert_safe_database_failure_log(
    caplog: pytest.LogCaptureFixture,
    *,
    provider: IntegrationProviderType,
    route: IntegrationWebhookRouteRecord,
    sensitive_values: tuple[str, ...],
) -> None:
    records = [
        record for record in caplog.records if record.getMessage() == "Communication webhook database operation failed"
    ]
    assert len(records) == 1
    record = records[0]
    assert record.__dict__["integration_provider"] == provider.value
    assert record.__dict__["webhook_purpose"] == "voice"
    assert record.__dict__["integration_id"] == route.integration_id
    assert record.__dict__["resource_id"] == route.resource_id
    assert record.__dict__["webhook_route_id"] == route.id
    assert record.__dict__["database_error_type"] == "SQLAlchemyError"
    assert record.exc_info is not None
    assert str(record.exc_info[1]) == "SQLAlchemyError details redacted"
    for sensitive_value in sensitive_values:
        assert sensitive_value not in caplog.text


def stub_telnyx_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    async def validate_credentials(
        self: TelnyxIntegrationProvider,
        credentials: TelnyxCredentialBundle,
    ) -> IntegrationCredentialValidation:
        return IntegrationCredentialValidation(
            valid=True,
            safe_metadata={
                "webhook_public_key": credentials.webhook_public_key,
            },
        )

    async def provision_phone_number(
        self: TelnyxIntegrationProvider,
        *,
        credentials: TelnyxCredentialBundle,
        phone_number: str,
        label: str,
        webhook_event_url: str,
        webhook_url: str,
    ) -> TelnyxPhoneNumberProvisioning:
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
            previous_number={},
            number={"phone_number": phone_number},
            voice_application={"id": TELNYX_CONNECTION_ID, "webhook_event_url": webhook_event_url},
            messaging_profile={"id": TELNYX_MESSAGING_PROFILE_ID, "webhook_url": webhook_url},
            outbound_voice_profile={"id": "outbound-profile-id"},
        )

    async def inspect_phone_number_configuration(
        self: TelnyxIntegrationProvider,
        *,
        credentials: TelnyxCredentialBundle,
        phone_number_id: str,
        outbound_voice_profile_id: str | None,
        expected_voice_url: str,
        expected_sms_url: str,
    ) -> TelnyxPhoneNumberConfiguration:
        del self, credentials, expected_voice_url, expected_sms_url
        return telnyx_configuration(
            phone_number_id=phone_number_id,
            outbound_voice_profile_id=outbound_voice_profile_id,
        )

    async def repair_phone_number_configuration(
        self: TelnyxIntegrationProvider,
        *,
        credentials: TelnyxCredentialBundle,
        phone_number_id: str,
        outbound_voice_profile_id: str,
        label: str,
        expected_voice_url: str,
        expected_sms_url: str,
    ) -> TelnyxPhoneNumberConfiguration:
        del self, credentials, label, expected_voice_url, expected_sms_url
        return telnyx_configuration(
            phone_number_id=phone_number_id,
            outbound_voice_profile_id=outbound_voice_profile_id,
        )

    monkeypatch.setattr(
        TelnyxIntegrationProvider,
        "validate_credentials",
        validate_credentials,
    )
    monkeypatch.setattr(
        TelnyxIntegrationProvider,
        "provision_phone_number",
        provision_phone_number,
    )
    monkeypatch.setattr(
        TelnyxIntegrationProvider,
        "inspect_phone_number_configuration",
        inspect_phone_number_configuration,
    )
    monkeypatch.setattr(
        TelnyxIntegrationProvider,
        "repair_phone_number_configuration",
        repair_phone_number_configuration,
    )


def telnyx_configuration(
    *,
    phone_number_id: str,
    outbound_voice_profile_id: str | None,
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
        outbound_voice_profile_id=outbound_voice_profile_id,
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
        messaging_destinations=("US",),
        voice_destinations=("US",),
    )

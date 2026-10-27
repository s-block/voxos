from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from voxos.agent_runtime.models import JsonObject
from voxos.integrations.models import (
    CommunicationChannel,
    IntegrationActionResponse,
    IntegrationAuthType,
    IntegrationCapability,
    IntegrationDirection,
    IntegrationProviderType,
    IntegrationWebhookPurpose,
    TwilioPhoneNumberCandidate,
)

CredentialBundleT_contra = TypeVar("CredentialBundleT_contra", contravariant=True)
NumberCandidateT_co = TypeVar("NumberCandidateT_co", covariant=True)


class IntegrationProviderDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: IntegrationProviderType
    label: str = Field(min_length=1, max_length=256)
    auth_types: tuple[IntegrationAuthType, ...]
    capabilities: tuple[IntegrationCapability, ...]


class IntegrationCredentialValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    status_message: str | None = Field(default=None, max_length=1024)
    safe_metadata: JsonObject = Field(default_factory=dict)


class NormalizedCommunicationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: IntegrationProviderType
    event_type: str = Field(min_length=1, max_length=128)
    channel: CommunicationChannel
    direction: IntegrationDirection
    external_event_id: str | None = Field(default=None, min_length=1, max_length=256)
    idempotency_key: str = Field(min_length=1, max_length=512)
    payload_json: JsonObject = Field(default_factory=dict)
    safe_summary_json: JsonObject = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WebhookVerificationRequest:
    provider: IntegrationProviderType
    purpose: IntegrationWebhookPurpose
    expected_url: str
    params: Mapping[str, str] | Iterable[tuple[str, str]]
    signature: str | None
    signing_secret: SecretStr


class WebhookVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    reason: str | None = Field(default=None, min_length=1, max_length=256)


@dataclass(frozen=True, slots=True)
class EventNormalizationRequest:
    provider: IntegrationProviderType
    purpose: IntegrationWebhookPurpose
    params: Mapping[str, str]
    provider_account_id: str
    resource_display_name: str | None = None


@dataclass(frozen=True, slots=True)
class TwilioCredentialBundle:
    account_sid: str
    api_key_sid: str
    api_key_secret: SecretStr
    auth_token: SecretStr


@dataclass(frozen=True, slots=True)
class TelnyxCredentialBundle:
    api_key: SecretStr
    webhook_public_key: str
    stream_auth_token: SecretStr
    connection_id: str | None = None
    messaging_profile_id: str | None = None


class TelnyxPhoneNumberProvisioning(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    phone_number_id: str = Field(min_length=1, max_length=256)
    connection_id: str = Field(min_length=1, max_length=256)
    messaging_profile_id: str = Field(min_length=1, max_length=256)
    outbound_voice_profile_id: str | None = Field(default=None, min_length=1, max_length=256)
    capabilities: tuple[IntegrationCapability, ...] = Field(default_factory=tuple)
    previous_number: JsonObject = Field(default_factory=dict)
    number: JsonObject = Field(default_factory=dict)
    voice_application: JsonObject = Field(default_factory=dict)
    messaging_profile: JsonObject = Field(default_factory=dict)
    outbound_voice_profile: JsonObject = Field(default_factory=dict)


class TelnyxPhoneNumberConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    phone_number_id: str = Field(min_length=1, max_length=256)
    phone_number_status: str | None = Field(default=None, min_length=1, max_length=64)
    capabilities: tuple[IntegrationCapability, ...] = Field(default_factory=tuple)
    connection_id: str | None = Field(default=None, min_length=1, max_length=256)
    messaging_profile_id: str | None = Field(default=None, min_length=1, max_length=256)
    outbound_voice_profile_id: str | None = Field(default=None, min_length=1, max_length=256)
    connection_assignment_matches: bool
    messaging_assignment_matches: bool
    outbound_voice_profile_assignment_matches: bool
    voice_application_found: bool
    voice_application_active: bool | None = None
    voice_webhook_matches: bool
    voice_webhook_api_version: str | None = Field(default=None, min_length=1, max_length=16)
    messaging_profile_found: bool
    messaging_profile_enabled: bool | None = None
    messaging_webhook_matches: bool
    messaging_webhook_api_version: str | None = Field(default=None, min_length=1, max_length=16)
    outbound_voice_profile_found: bool
    outbound_voice_profile_enabled: bool | None = None
    messaging_destinations: tuple[str, ...] = Field(default_factory=tuple)
    voice_destinations: tuple[str, ...] = Field(default_factory=tuple)


class TelnyxPhoneNumberCleanup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    phone_number_id: str = Field(min_length=1, max_length=256)
    connection_id: str | None = Field(default=None, min_length=1, max_length=256)
    messaging_profile_id: str | None = Field(default=None, min_length=1, max_length=256)
    outbound_voice_profile_id: str | None = Field(default=None, min_length=1, max_length=256)
    previous_connection_id: str | None = Field(default=None, min_length=1, max_length=256)
    previous_messaging_profile_id: str | None = Field(default=None, min_length=1, max_length=256)


class ProviderClient(Protocol[CredentialBundleT_contra]):
    def definition(self) -> IntegrationProviderDefinition:
        """Return static provider metadata."""

    async def validate_credentials(
        self,
        credentials: CredentialBundleT_contra,
    ) -> IntegrationCredentialValidation:
        """Validate provider credentials without exposing secrets."""


class NumberProvisioningProvider(Protocol[CredentialBundleT_contra, NumberCandidateT_co]):
    async def list_phone_numbers(
        self,
        credentials: CredentialBundleT_contra,
    ) -> tuple[NumberCandidateT_co, ...]:
        """List connectable phone-number resources."""


class PhoneNumberWebhookProvider(Protocol[CredentialBundleT_contra, NumberCandidateT_co]):
    async def configure_phone_number_webhooks(
        self,
        *,
        credentials: CredentialBundleT_contra,
        phone_number_id: str,
        voice_url: str | None,
        voice_method: str | None,
        sms_url: str | None,
        sms_method: str | None,
        status_callback: str | None,
        status_callback_method: str | None,
        clear_missing_urls: bool = False,
    ) -> NumberCandidateT_co:
        """Configure provider webhook settings for a phone number."""


class SMSProvider(Protocol[CredentialBundleT_contra]):
    async def send_sms(
        self,
        *,
        credentials: CredentialBundleT_contra,
        from_number: str,
        to_number: str,
        body: str,
        status_callback_url: str | None,
    ) -> IntegrationActionResponse:
        """Send an SMS message."""


class VoiceProvider(Protocol[CredentialBundleT_contra]):
    async def start_outbound_call(
        self,
        *,
        credentials: CredentialBundleT_contra,
        from_number: str,
        to_number: str,
        twiml_url: str,
        status_callback_url: str | None,
    ) -> IntegrationActionResponse:
        """Start a provider voice call."""


class TelnyxProviderCapabilities(
    ProviderClient[TelnyxCredentialBundle],
    SMSProvider[TelnyxCredentialBundle],
    VoiceProvider[TelnyxCredentialBundle],
    Protocol,
):
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
        """Start a Telnyx call with optional provider-side correlation."""

    async def provision_phone_number(
        self,
        *,
        credentials: TelnyxCredentialBundle,
        phone_number: str,
        label: str,
        webhook_event_url: str,
        webhook_url: str,
    ) -> TelnyxPhoneNumberProvisioning:
        """Create dedicated Telnyx routing objects and assign a number to them."""

    async def release_phone_number(
        self,
        *,
        credentials: TelnyxCredentialBundle,
        cleanup: TelnyxPhoneNumberCleanup,
    ) -> None:
        """Restore number routing and remove Voxos-created Telnyx routing objects."""

    async def inspect_phone_number_configuration(
        self,
        *,
        credentials: TelnyxCredentialBundle,
        phone_number_id: str,
        outbound_voice_profile_id: str | None,
        expected_voice_url: str,
        expected_sms_url: str,
    ) -> TelnyxPhoneNumberConfiguration:
        """Read a safe snapshot of the number and Voxos-owned routing objects."""

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
        """Idempotently restore assignments and settings on existing routing objects."""

    async def answer_call(
        self,
        *,
        credentials: TelnyxCredentialBundle,
        call_control_id: str,
        stream_url: str,
        webhook_url: str | None,
        command_id: str,
    ) -> IntegrationActionResponse:
        """Answer an inbound Telnyx call and start media streaming."""

    async def answer_verification_call(
        self,
        *,
        credentials: TelnyxCredentialBundle,
        call_control_id: str,
        webhook_url: str | None,
        command_id: str,
    ) -> IntegrationActionResponse:
        """Answer an inbound verification call and transcribe its inbound audio."""

    async def send_call_dtmf(
        self,
        *,
        credentials: TelnyxCredentialBundle,
        call_control_id: str,
        digits: str,
        command_id: str,
    ) -> IntegrationActionResponse:
        """Send DTMF digits to the remote party on an active call."""


class WebhookVerifier(Protocol):
    def validate_webhook(
        self,
        request: WebhookVerificationRequest,
    ) -> WebhookVerificationResult:
        """Validate a provider webhook signature before payload trust."""


class EventNormalizer(Protocol):
    async def normalize_event(
        self,
        request: EventNormalizationRequest,
    ) -> NormalizedCommunicationEvent:
        """Normalize a provider-specific webhook payload."""


class TwilioProviderCapabilities(
    ProviderClient[TwilioCredentialBundle],
    NumberProvisioningProvider[TwilioCredentialBundle, TwilioPhoneNumberCandidate],
    PhoneNumberWebhookProvider[TwilioCredentialBundle, TwilioPhoneNumberCandidate],
    SMSProvider[TwilioCredentialBundle],
    VoiceProvider[TwilioCredentialBundle],
    Protocol,
):
    async def list_resources(
        self,
        credentials: TwilioCredentialBundle,
    ) -> tuple[TwilioPhoneNumberCandidate, ...]:
        """Compatibility wrapper for existing Twilio callers."""

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
        """Update webhook settings for a Twilio incoming phone number."""

    async def send_message(
        self,
        *,
        credentials: TwilioCredentialBundle,
        from_number: str,
        to_number: str,
        body: str,
        status_callback_url: str | None,
    ) -> IntegrationActionResponse:
        """Send a provider message."""

    async def start_outbound_call(
        self,
        *,
        credentials: TwilioCredentialBundle,
        from_number: str,
        to_number: str,
        twiml_url: str,
        status_callback_url: str | None,
    ) -> IntegrationActionResponse:
        """Start a provider voice call."""

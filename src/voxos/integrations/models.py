import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from voxos.agent_runtime.models import JsonObject
from voxos.logos.models import BrandLogo
from voxos.utils.phone_numbers import normalize_e164_phone_number

TWILIO_ACCOUNT_SID_PATTERN = re.compile(r"^AC[a-fA-F0-9]{32}$")
TWILIO_API_KEY_SID_PATTERN = re.compile(r"^SK[a-fA-F0-9]{32}$")
TWILIO_PHONE_NUMBER_SID_PATTERN = re.compile(r"^PN[a-fA-F0-9]{32}$")


class IntegrationProviderType(StrEnum):
    TWILIO = "twilio"
    TELNYX = "telnyx"
    WHATSAPP = "whatsapp"


class CommunicationChannel(StrEnum):
    VOICE = "voice"
    SMS = "sms"
    WHATSAPP = "whatsapp"


class IntegrationCapability(StrEnum):
    WEBHOOK_EVENTS = "webhook_events"
    INBOUND_VOICE = "inbound_voice"
    OUTBOUND_VOICE = "outbound_voice"
    SMS_READ = "sms_read"
    SMS_SEND = "sms_send"
    WHATSAPP_READ = "whatsapp_read"
    WHATSAPP_SEND = "whatsapp_send"
    MEDIA_STREAM = "media_stream"
    TOOL_ACTIONS = "tool_actions"


class IntegrationAuthType(StrEnum):
    API_KEY = "api_key"  # pragma: allowlist secret
    LINKED_DEVICE = "linked_device"


class IntegrationStatus(StrEnum):
    DISABLED = "disabled"
    NEEDS_AUTH = "needs_auth"
    UNTESTED = "untested"
    CONNECTED = "connected"
    FAILED = "failed"
    REVOKED = "revoked"


class IntegrationCredentialStatus(StrEnum):
    ACTIVE = "active"
    FAILED = "failed"
    REVOKED = "revoked"


class IntegrationCredentialKind(StrEnum):
    TWILIO_API_KEY_SECRET = "twilio_api_key_secret"  # pragma: allowlist secret
    TWILIO_AUTH_TOKEN = "twilio_auth_token"
    PROVIDER_API_SECRET = "provider_api_secret"  # pragma: allowlist secret
    TELNYX_STREAM_AUTH_TOKEN = "telnyx_stream_auth_token"  # pragma: allowlist secret
    WHATSAPP_SESSION_DATA_KEY = "whatsapp_session_data_key"  # pragma: allowlist secret


class IntegrationResourceStatus(StrEnum):
    CONNECTED = "connected"
    DISABLED = "disabled"
    FAILED = "failed"
    FAILED_DISCONNECT_CLEANUP = "failed_disconnect_cleanup"


class IntegrationResourceType(StrEnum):
    PHONE_NUMBER = "phone_number"


class IntegrationWebhookPurpose(StrEnum):
    VOICE = "voice"
    SMS = "sms"
    STATUS = "status"
    MEDIA_STREAM = "media_stream"


class IntegrationRouteStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class IntegrationDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    UNKNOWN = "unknown"


class IntegrationEventStatus(StrEnum):
    ACCEPTED = "accepted"
    PROCESSING = "processing"
    REJECTED = "rejected"
    PROCESSED = "processed"
    FAILED = "failed"


class IntegrationMessageStatus(StrEnum):
    RECEIVED = "received"
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    DELIVERY_UNCONFIRMED = "delivery_unconfirmed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class IntegrationReplyStatus(StrEnum):
    PENDING = "pending"
    GENERATED = "generated"
    DISPATCHING = "dispatching"
    PROVIDER_ACCEPTED = "provider_accepted"
    DELIVERED = "delivered"
    FAILED = "failed"
    SUPPRESSED = "suppressed"
    AMBIGUOUS = "ambiguous"


class IntegrationCallStatus(StrEnum):
    INITIATING = "initiating"
    RINGING = "ringing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


class IntegrationCallOpeningState(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class IntegrationReadinessStatus(StrEnum):
    READY = "ready"
    NOT_READY = "not_ready"
    UNKNOWN = "unknown"


class IntegrationTranscriptStatus(StrEnum):
    DISABLED = "disabled"
    ENABLED = "enabled"
    FAILED = "failed"


class IntegrationAuditAction(StrEnum):
    INTEGRATION_CREATED = "integration_created"
    CREDENTIALS_SAVED = "credentials_saved"
    RESOURCE_CONNECTED = "resource_connected"
    WEBHOOK_OVERWRITE_CONFIRMED = "webhook_overwrite_confirmed"
    WEBHOOK_CONFIGURED = "webhook_configured"
    WEBHOOK_REJECTED = "webhook_rejected"
    EVENT_ACCEPTED = "event_accepted"
    TOOL_ALLOWED = "tool_allowed"
    TOOL_DENIED = "tool_denied"
    SMS_SENT = "sms_sent"
    WHATSAPP_LINKED = "whatsapp_linked"
    WHATSAPP_RELINKED = "whatsapp_relinked"
    WHATSAPP_SENT = "whatsapp_sent"
    WHATSAPP_SESSION_NEEDS_RELINK = "whatsapp_session_needs_relink"
    CALL_STARTED = "call_started"
    INTEGRATION_DISCONNECTED = "integration_disconnected"


class IntegrationAuditActorType(StrEnum):
    USER = "user"
    AGENT = "agent"
    WEBHOOK = "webhook"
    SYSTEM = "system"


class IntegrationAuditOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"


class IntegrationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    provider: IntegrationProviderType
    label: str = Field(min_length=1, max_length=256)
    status: IntegrationStatus
    enabled: bool
    auth_type: IntegrationAuthType
    setup_state: str = Field(min_length=1, max_length=64)
    default_agent_id: str | None = Field(default=None, min_length=1, max_length=36)
    safe_metadata: JsonObject = Field(default_factory=dict)
    logo: BrandLogo
    last_checked_at: datetime | None = None
    status_message: str | None = Field(default=None, max_length=1024)
    created_at: datetime
    updated_at: datetime


class IntegrationListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    integrations: tuple[IntegrationResponse, ...] = Field(default_factory=tuple)


class IntegrationCredentialSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    secret_kind: IntegrationCredentialKind
    status: IntegrationCredentialStatus
    public_identifier: str | None = Field(default=None, min_length=1, max_length=256)
    last_four: str | None = Field(default=None, min_length=1, max_length=8)
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None = None


class IntegrationResourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    integration_id: str = Field(min_length=1, max_length=36)
    provider: IntegrationProviderType
    resource_type: IntegrationResourceType
    external_id: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=256)
    status: IntegrationResourceStatus
    capabilities: tuple[IntegrationCapability, ...] = Field(default_factory=tuple)
    safe_metadata: JsonObject = Field(default_factory=dict)
    retained_metadata_json: JsonObject = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class IntegrationResourceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resources: tuple[IntegrationResourceResponse, ...] = Field(default_factory=tuple)


class TelnyxPhoneSetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(default="Telnyx", min_length=1, max_length=256)
    phone_number: str = Field(min_length=1, max_length=32)
    api_key: SecretStr = Field(min_length=1)
    webhook_public_key: str = Field(min_length=1, max_length=4096)
    confirm_number_assignment: bool
    transcript_storage_enabled: bool = False

    @field_validator(
        "label",
        "phone_number",
        "webhook_public_key",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("phone_number")
    @classmethod
    def validate_phone_number(cls, value: str) -> str:
        return normalize_e164_phone_number(value)


class TelnyxPhoneSetupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    integration: IntegrationResponse
    credentials: tuple[IntegrationCredentialSummary, ...] = Field(default_factory=tuple)
    resources: tuple[IntegrationResourceResponse, ...] = Field(default_factory=tuple)


class TelnyxRepairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    confirm_provider_changes: bool


class IntegrationReadinessCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=64)
    status: IntegrationReadinessStatus
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=512)


class TelnyxProviderReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    phone_number_id: str = Field(min_length=1, max_length=256)
    phone_number: str = Field(min_length=1, max_length=32)
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


class TelnyxReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    integration_id: str = Field(min_length=1, max_length=36)
    resource_id: str = Field(min_length=1, max_length=36)
    checked_at: datetime
    repaired: bool = False
    provider: TelnyxProviderReadiness
    checks: tuple[IntegrationReadinessCheck, ...]


class IntegrationMessageActivity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    integration_id: str = Field(min_length=1, max_length=36)
    resource_id: str = Field(min_length=1, max_length=36)
    provider: IntegrationProviderType
    direction: IntegrationDirection
    remote_party: str | None = Field(default=None, min_length=1, max_length=32)
    status: IntegrationMessageStatus
    external_message_id: str = Field(min_length=1, max_length=256)
    agent_run_id: str | None = Field(default=None, min_length=1, max_length=36)
    agent_run_status: str | None = Field(default=None, min_length=1, max_length=64)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=36)
    reply_id: str | None = Field(default=None, min_length=1, max_length=36)
    reply_status: IntegrationReplyStatus | None = None
    response_profile: str | None = Field(default=None, min_length=1, max_length=64)
    sms_encoding: str | None = Field(default=None, min_length=1, max_length=32)
    estimated_segments: int | None = Field(default=None, ge=0)
    condensation_attempted: bool = False
    overflow_fallback_used: bool = False
    failure_code: str | None = Field(default=None, min_length=1, max_length=128)
    failure_title: str | None = Field(default=None, min_length=1, max_length=256)
    created_at: datetime
    updated_at: datetime


class IntegrationMessageActivityListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    messages: tuple[IntegrationMessageActivity, ...] = Field(default_factory=tuple)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    next_offset: int | None = Field(default=None, ge=0)


class IntegrationCallActivity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    integration_id: str = Field(min_length=1, max_length=36)
    resource_id: str = Field(min_length=1, max_length=36)
    provider: IntegrationProviderType
    direction: IntegrationDirection
    remote_party: str | None = Field(default=None, min_length=1, max_length=32)
    status: IntegrationCallStatus
    external_call_id: str = Field(min_length=1, max_length=256)
    contact_id: str | None = Field(default=None, min_length=1, max_length=36)
    contact_display_name: str | None = Field(default=None, min_length=1, max_length=256)
    agent_id: str | None = Field(default=None, min_length=1, max_length=36)
    failure_code: str | None = Field(default=None, min_length=1, max_length=128)
    started_at: datetime | None = None
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class IntegrationCallActivityListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    calls: tuple[IntegrationCallActivity, ...] = Field(default_factory=tuple)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    next_offset: int | None = Field(default=None, ge=0)


class IntegrationCallLatencyPercentiles(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_count: int = Field(ge=0)
    p50_ms: int | None = Field(default=None, ge=0)
    p95_ms: int | None = Field(default=None, ge=0)


class IntegrationCallLatencyReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    integration_id: str = Field(min_length=1, max_length=36)
    provider: IntegrationProviderType | None = None
    worker_registration_to_agent_ready: IntegrationCallLatencyPercentiles
    agent_ready_to_provider_dial: IntegrationCallLatencyPercentiles
    provider_dial_to_answer: IntegrationCallLatencyPercentiles
    provider_dial_to_media_ready: IntegrationCallLatencyPercentiles
    answer_to_first_speech: IntegrationCallLatencyPercentiles
    opening_scheduled_to_playout_complete: IntegrationCallLatencyPercentiles


class TwilioCredentialSetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(default="Twilio", min_length=1, max_length=256)
    account_sid: str = Field(min_length=34, max_length=34)
    api_key_sid: str = Field(min_length=34, max_length=34)
    api_key_secret: SecretStr = Field(min_length=1)
    auth_token: SecretStr = Field(min_length=1)

    @field_validator("label", "account_sid", "api_key_sid", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("account_sid")
    @classmethod
    def validate_account_sid(cls, value: str) -> str:
        if TWILIO_ACCOUNT_SID_PATTERN.fullmatch(value) is None:
            message = "account_sid must be a Twilio Account SID"
            raise ValueError(message)
        return value

    @field_validator("api_key_sid")
    @classmethod
    def validate_api_key_sid(cls, value: str) -> str:
        if TWILIO_API_KEY_SID_PATTERN.fullmatch(value) is None:
            message = "api_key_sid must be a Twilio API Key SID"
            raise ValueError(message)
        return value


class TwilioPhoneNumberCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sid: str = Field(min_length=1, max_length=64)
    phone_number: str = Field(min_length=1, max_length=32)
    friendly_name: str | None = Field(default=None, min_length=1, max_length=256)
    capabilities: tuple[IntegrationCapability, ...] = Field(default_factory=tuple)
    voice_url: str | None = Field(default=None, max_length=2048)
    voice_method: str | None = Field(default=None, max_length=16)
    sms_url: str | None = Field(default=None, max_length=2048)
    sms_method: str | None = Field(default=None, max_length=16)
    status_callback: str | None = Field(default=None, max_length=2048)
    status_callback_method: str | None = Field(default=None, max_length=16)

    @field_validator("sid")
    @classmethod
    def validate_sid(cls, value: str) -> str:
        if TWILIO_PHONE_NUMBER_SID_PATTERN.fullmatch(value) is None:
            message = "sid must be a Twilio phone number SID"
            raise ValueError(message)
        return value


class CommunicationOutboundSmsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    integration_id: str = Field(min_length=1, max_length=36)
    resource_id: str = Field(min_length=1, max_length=36)
    agent_id: str = Field(min_length=1, max_length=36)
    to_number: str = Field(min_length=1, max_length=32)
    body: str = Field(min_length=1, max_length=1600)

    @field_validator("to_number")
    @classmethod
    def validate_to_number(cls, value: str) -> str:
        return normalize_e164_phone_number(value, field_name="to_number")


class CommunicationOutboundCallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    integration_id: str = Field(min_length=1, max_length=36)
    resource_id: str = Field(min_length=1, max_length=36)
    agent_id: str = Field(min_length=1, max_length=36)
    to_number: str = Field(min_length=1, max_length=32)
    objective: str = Field(min_length=1, max_length=4000)
    initiating_run_id: str | None = Field(default=None, min_length=1, max_length=36)

    @field_validator("to_number")
    @classmethod
    def validate_to_number(cls, value: str) -> str:
        return normalize_e164_phone_number(value, field_name="to_number")

    @field_validator("objective", mode="before")
    @classmethod
    def strip_objective(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class TwilioCredentialSetupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    integration: IntegrationResponse
    credentials: tuple[IntegrationCredentialSummary, ...]
    phone_numbers: tuple[TwilioPhoneNumberCandidate, ...]


class TwilioResourceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    phone_number_sid: str = Field(min_length=1, max_length=64)
    capabilities: tuple[IntegrationCapability, ...] = Field(
        default=(
            IntegrationCapability.INBOUND_VOICE,
            IntegrationCapability.OUTBOUND_VOICE,
            IntegrationCapability.SMS_READ,
            IntegrationCapability.SMS_SEND,
        ),
    )
    transcript_storage_enabled: bool = False

    @field_validator("phone_number_sid", mode="before")
    @classmethod
    def strip_sid(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("phone_number_sid")
    @classmethod
    def validate_phone_number_sid(cls, value: str) -> str:
        if TWILIO_PHONE_NUMBER_SID_PATTERN.fullmatch(value) is None:
            message = "phone_number_sid must be a Twilio phone number SID"
            raise ValueError(message)
        return value


class TwilioResourceConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resources: tuple[TwilioResourceSelection, ...] = Field(min_length=1)
    overwrite_existing_webhooks: bool


class TwilioResourceConnectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    integration: IntegrationResponse
    resources: tuple[IntegrationResourceResponse, ...]


class TwilioOutboundSmsRequest(CommunicationOutboundSmsRequest):
    pass


class TwilioOutboundCallRequest(CommunicationOutboundCallRequest):
    pass


class IntegrationActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    provider: IntegrationProviderType
    action: str = Field(min_length=1, max_length=128)
    external_id: str | None = Field(default=None, min_length=1, max_length=256)
    status: str = Field(min_length=1, max_length=64)
    safe_metadata: JsonObject = Field(default_factory=dict)


def last_four(value: str) -> str:
    stripped = value.strip()
    return stripped[-4:] if len(stripped) >= 4 else stripped

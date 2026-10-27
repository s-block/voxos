from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

WHATSAPP_MAX_TEXT_BYTES = 16 * 1024


class WhatsAppLinkMethod(StrEnum):
    QR = "qr"
    PAIRING_CODE = "pairing_code"


class WhatsAppMobileRegistrationChannel(StrEnum):
    SMS = "sms"
    VOICE = "voice"


class WhatsAppMobileRegistrationState(StrEnum):
    ARMED = "armed"
    ARMED_VOICE = "armed_voice"
    CODE_RECEIVED = "code_received"
    EXPIRED = "expired"


class WhatsAppDesiredState(StrEnum):
    CONNECTED = "connected"
    STOPPED = "stopped"


class WhatsAppSessionState(StrEnum):
    AWAITING_LINK = "awaiting_link"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    WAITING_FOR_CAPACITY = "waiting_for_capacity"
    NEEDS_RELINK = "needs_relink"
    DISCONNECTING = "disconnecting"
    FAILED = "failed"
    DISABLED = "disabled"


class WhatsAppOutboxStatus(StrEnum):
    QUEUED = "queued"
    SUBMITTING = "submitting"
    SENT = "sent"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


class WhatsAppContactResolutionState(StrEnum):
    UNLINKED = "unlinked"
    LINKED = "linked"
    CONFLICT = "conflict"


class WhatsAppSetupOption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_resource_id: str = Field(min_length=1, max_length=36)
    provider: str = Field(min_length=1, max_length=64)
    phone_number: str = Field(min_length=1, max_length=32)
    line_type: str | None = Field(default=None, min_length=1, max_length=64)


class WhatsAppSetupOptionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    options: tuple[WhatsAppSetupOption, ...] = Field(default_factory=tuple)


class WhatsAppLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_resource_id: str = Field(min_length=1, max_length=36)
    method: WhatsAppLinkMethod


class WhatsAppMobileRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_resource_id: str = Field(min_length=1, max_length=36)
    channel: WhatsAppMobileRegistrationChannel = WhatsAppMobileRegistrationChannel.SMS


class WhatsAppMobileRegistrationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(min_length=1, max_length=256)
    code: str = Field(pattern=r"^\d{4,8}$")
    channel: WhatsAppMobileRegistrationChannel
    sender: str = Field(min_length=1, max_length=32)
    received_at: datetime


class WhatsAppMobileRegistrationStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    registration_id: str = Field(min_length=1, max_length=36)
    source_resource_id: str = Field(min_length=1, max_length=36)
    phone_number: str = Field(min_length=1, max_length=32)
    state: WhatsAppMobileRegistrationState
    capture_expires_at: datetime
    candidates: tuple[WhatsAppMobileRegistrationCandidate, ...] = Field(
        default_factory=tuple,
        max_length=5,
    )


class WhatsAppMobileRegistrationCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(min_length=1, max_length=256)


class WhatsAppMobileRegistrationCompleteRequest(
    WhatsAppMobileRegistrationCandidateRequest,
):
    method: WhatsAppLinkMethod


class WhatsAppChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    method: WhatsAppLinkMethod


class WhatsAppStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    integration_id: str = Field(min_length=1, max_length=36)
    source_resource_id: str = Field(min_length=1, max_length=36)
    phone_number: str = Field(min_length=1, max_length=32)
    state: WhatsAppSessionState
    status_message: str | None = Field(default=None, max_length=1024)
    validated_at: datetime | None = None
    connected_at: datetime | None = None
    challenge_expires_at: datetime | None = None


class WhatsAppChallengeResponse(WhatsAppStatusResponse):
    method: WhatsAppLinkMethod
    value: str = Field(min_length=1, max_length=4096)


class WhatsAppWorkerHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    worker_id: str = Field(min_length=1, max_length=128)
    bridge_connected: bool
    active_sessions: int = Field(ge=0)
    last_successful_poll_at: datetime
    updated_at: datetime

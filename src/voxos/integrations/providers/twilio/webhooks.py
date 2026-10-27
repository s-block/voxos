from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass

from twilio.request_validator import RequestValidator

from voxos.integrations.errors import IntegrationWebhookError
from voxos.integrations.models import (
    CommunicationChannel,
    IntegrationDirection,
    IntegrationProviderType,
    IntegrationWebhookPurpose,
)
from voxos.integrations.providers.base import (
    EventNormalizationRequest,
    NormalizedCommunicationEvent,
    WebhookVerificationRequest,
    WebhookVerificationResult,
)

TWILIO_MESSAGE_SID_MISSING_ERROR = "Twilio message SID is missing."
TWILIO_CALL_SID_MISSING_ERROR = "Twilio call SID is missing."
TWILIO_STATUS_EVENT_ID_MISSING_ERROR = "Twilio status event ID is missing."
TWILIO_SIGNATURE_INVALID_ERROR = "Twilio webhook signature is invalid."
TwilioSignatureParams = Mapping[str, str] | Iterable[tuple[str, str]]


@dataclass(frozen=True, slots=True)
class TwilioMultiValueParams:
    pairs: tuple[tuple[str, str], ...]

    def __iter__(self) -> Iterator[str]:
        return (key for key, _value in self.pairs)

    def getall(self, param_name: str) -> tuple[str, ...]:
        return tuple(value for key, value in self.pairs if key == param_name)


@dataclass(frozen=True, slots=True)
class TwilioWebhookVerifier:
    def validate_webhook(
        self,
        request: WebhookVerificationRequest,
    ) -> WebhookVerificationResult:
        validator = RequestValidator(request.signing_secret.get_secret_value())
        valid = request.signature is not None and validator.validate(
            request.expected_url,
            twilio_validator_params(request.params),
            request.signature,
        )
        if valid:
            return WebhookVerificationResult(valid=True)
        return WebhookVerificationResult(
            valid=False,
            reason=TWILIO_SIGNATURE_INVALID_ERROR,
        )


@dataclass(frozen=True, slots=True)
class TwilioEventNormalizer:
    async def normalize_event(
        self,
        request: EventNormalizationRequest,
    ) -> NormalizedCommunicationEvent:
        if request.purpose == IntegrationWebhookPurpose.VOICE:
            return normalize_voice_start(request)
        if request.purpose == IntegrationWebhookPurpose.SMS:
            return normalize_sms_inbound(request)
        if request.purpose == IntegrationWebhookPurpose.STATUS:
            return normalize_status(request)
        message = f"Unsupported Twilio webhook purpose: {request.purpose.value}"
        raise IntegrationWebhookError(message)


def normalize_sms_inbound(
    request: EventNormalizationRequest,
) -> NormalizedCommunicationEvent:
    message_sid = request.params.get("MessageSid") or request.params.get("SmsSid")
    if message_sid is None or not message_sid.strip():
        raise IntegrationWebhookError(TWILIO_MESSAGE_SID_MISSING_ERROR)
    message_sid = message_sid.strip()
    return NormalizedCommunicationEvent(
        provider=IntegrationProviderType.TWILIO,
        event_type="sms_inbound",
        channel=CommunicationChannel.SMS,
        direction=IntegrationDirection.INBOUND,
        external_event_id=message_sid,
        idempotency_key=f"twilio:{request.provider_account_id}:message:{message_sid}:inbound",
        payload_json=dict(request.params),
        safe_summary_json={
            "message_sid": message_sid,
            "from": request.params.get("From"),
            "to": request.params.get("To"),
            "direction": IntegrationDirection.INBOUND.value,
        },
    )


def normalize_voice_start(
    request: EventNormalizationRequest,
) -> NormalizedCommunicationEvent:
    call_sid = required_param(request.params, "CallSid", TWILIO_CALL_SID_MISSING_ERROR)
    direction = call_direction(
        params=request.params,
        resource_display_name=request.resource_display_name,
    )
    return NormalizedCommunicationEvent(
        provider=IntegrationProviderType.TWILIO,
        event_type="voice_start",
        channel=CommunicationChannel.VOICE,
        direction=direction,
        external_event_id=call_sid,
        idempotency_key=f"twilio:{request.provider_account_id}:call:{call_sid}:start",
        payload_json=dict(request.params),
        safe_summary_json={
            "call_sid": call_sid,
            "from": request.params.get("From") or request.params.get("Caller"),
            "to": request.params.get("To") or request.params.get("Called"),
            "direction": direction.value,
        },
    )


def normalize_status(
    request: EventNormalizationRequest,
) -> NormalizedCommunicationEvent:
    call_sid = request.params.get("CallSid")
    message_sid = request.params.get("MessageSid") or request.params.get("SmsSid")
    event_type = "status"
    channel = CommunicationChannel.VOICE
    external_id = call_sid or message_sid
    if call_sid is not None:
        event_type = "voice_status"
        channel = CommunicationChannel.VOICE
    elif message_sid is not None:
        event_type = "sms_status"
        channel = CommunicationChannel.SMS
    if external_id is None or not external_id.strip():
        raise IntegrationWebhookError(TWILIO_STATUS_EVENT_ID_MISSING_ERROR)
    status = (
        request.params.get("CallStatus")
        or request.params.get("MessageStatus")
        or request.params.get("SmsStatus")
        or "unknown"
    )
    return NormalizedCommunicationEvent(
        provider=IntegrationProviderType.TWILIO,
        event_type=event_type,
        channel=channel,
        direction=IntegrationDirection.UNKNOWN,
        external_event_id=external_id,
        idempotency_key=f"twilio:{request.provider_account_id}:{event_type}:{external_id}:{status}",
        payload_json=dict(request.params),
        safe_summary_json={
            "event_type": event_type,
            "external_id": external_id,
            "call_status": request.params.get("CallStatus"),
            "message_status": request.params.get("MessageStatus") or request.params.get("SmsStatus"),
        },
    )


def twilio_validator_params(
    params: TwilioSignatureParams,
) -> Mapping[str, str] | TwilioMultiValueParams:
    if isinstance(params, Mapping):
        return params
    return TwilioMultiValueParams(tuple(params))


def call_direction(
    *,
    params: Mapping[str, str],
    resource_display_name: str | None,
) -> IntegrationDirection:
    twilio_direction = params.get("Direction")
    if twilio_direction == "inbound":
        return IntegrationDirection.INBOUND
    if twilio_direction in {"outbound-api", "outbound-dial"}:
        return IntegrationDirection.OUTBOUND
    if resource_display_name is None:
        return IntegrationDirection.UNKNOWN
    if params.get("To") == resource_display_name or params.get("Called") == resource_display_name:
        return IntegrationDirection.INBOUND
    if params.get("From") == resource_display_name or params.get("Caller") == resource_display_name:
        return IntegrationDirection.OUTBOUND
    return IntegrationDirection.UNKNOWN


def required_param(
    params: Mapping[str, str],
    key: str,
    error_message: str,
) -> str:
    value = params.get(key)
    if value is None or not value.strip():
        raise IntegrationWebhookError(error_message)
    return value.strip()

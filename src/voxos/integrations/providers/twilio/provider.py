from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from twilio.base.exceptions import TwilioException
from twilio.http.async_http_client import AsyncTwilioHttpClient
from twilio.rest import Client

from voxos.integrations.models import (
    IntegrationActionResponse,
    IntegrationAuthType,
    IntegrationCapability,
    IntegrationProviderType,
    TwilioPhoneNumberCandidate,
)
from voxos.integrations.providers.base import (
    IntegrationCredentialValidation,
    IntegrationProviderDefinition,
    TwilioCredentialBundle,
)

TWILIO_PROVIDER_DEFINITION = IntegrationProviderDefinition(
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
TWILIO_API_REQUEST_FAILED_ERROR = "Twilio API request failed."
TWILIO_OUTBOUND_RING_TIMEOUT_SECONDS = 30


class TwilioProviderError(ValueError):
    """Raised when the official Twilio SDK rejects a provider operation."""


@dataclass(frozen=True, slots=True)
class TwilioIntegrationProvider:
    def definition(self) -> IntegrationProviderDefinition:
        return TWILIO_PROVIDER_DEFINITION

    async def validate_credentials(
        self,
        credentials: TwilioCredentialBundle,
    ) -> IntegrationCredentialValidation:
        try:
            async with twilio_sdk_client(credentials=credentials) as client:
                account = await client.api.accounts(credentials.account_sid).fetch_async()
            async with twilio_sdk_client(credentials=credentials, use_auth_token=True) as auth_client:
                auth_token_account = await auth_client.api.accounts(
                    credentials.account_sid,
                ).fetch_async()
        except TwilioException as exc:
            raise TwilioProviderError(twilio_exception_message(exc)) from exc
        if string_attr(auth_token_account, "sid") != credentials.account_sid:
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
            status_message=None,
            safe_metadata={
                "account_sid": credentials.account_sid,
                "api_key_sid": credentials.api_key_sid,
                "account_friendly_name": string_attr(account, "friendly_name"),
                "account_status": string_attr(account, "status"),
            },
        )

    async def list_phone_numbers(
        self,
        credentials: TwilioCredentialBundle,
    ) -> tuple[TwilioPhoneNumberCandidate, ...]:
        try:
            async with twilio_sdk_client(credentials=credentials) as client:
                numbers = await client.api.accounts(
                    credentials.account_sid,
                ).incoming_phone_numbers.list_async()
        except TwilioException as exc:
            raise TwilioProviderError(twilio_exception_message(exc)) from exc
        return tuple(phone_number_candidate_from_twilio(number) for number in numbers)

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
        try:
            async with twilio_sdk_client(credentials=credentials) as client:
                number = (
                    await client.api.accounts(
                        credentials.account_sid,
                    )
                    .incoming_phone_numbers(phone_number_id)
                    .update_async(
                        **webhook_update_kwargs(
                            voice_url=voice_url,
                            voice_method=voice_method,
                            sms_url=sms_url,
                            sms_method=sms_method,
                            status_callback=status_callback,
                            status_callback_method=status_callback_method,
                            clear_missing_urls=clear_missing_urls,
                        ),
                    )
                )
        except TwilioException as exc:
            raise TwilioProviderError(twilio_exception_message(exc)) from exc
        return phone_number_candidate_from_twilio(number)

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
        try:
            async with twilio_sdk_client(credentials=credentials) as client:
                message = await client.messages.create_async(
                    to=to_number,
                    from_=from_number,
                    body=body,
                    **optional_message_status_callback(status_callback_url),
                )
        except TwilioException as exc:
            raise TwilioProviderError(twilio_exception_message(exc)) from exc
        return action_response_for_message(message)

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
        try:
            async with twilio_sdk_client(credentials=credentials) as client:
                call = await client.calls.create_async(
                    to=to_number,
                    from_=from_number,
                    url=twiml_url,
                    method="POST",
                    timeout=TWILIO_OUTBOUND_RING_TIMEOUT_SECONDS,
                    **optional_call_status_callback(status_callback_url),
                )
        except TwilioException as exc:
            raise TwilioProviderError(twilio_exception_message(exc)) from exc
        return action_response_for_call(call)


@asynccontextmanager
async def twilio_sdk_client(
    *,
    credentials: TwilioCredentialBundle,
    use_auth_token: bool = False,
) -> AsyncIterator[Client]:
    http_client = AsyncTwilioHttpClient()
    username = credentials.account_sid if use_auth_token else credentials.api_key_sid
    secret = credentials.auth_token if use_auth_token else credentials.api_key_secret
    try:
        yield Client(
            username=username,
            password=secret.get_secret_value(),
            account_sid=credentials.account_sid,
            http_client=http_client,
        )
    finally:
        await http_client.close()


def phone_number_candidate_from_twilio(number: object) -> TwilioPhoneNumberCandidate:
    return TwilioPhoneNumberCandidate(
        sid=required_string_attr(number, "sid"),
        phone_number=required_string_attr(number, "phone_number"),
        friendly_name=string_attr(number, "friendly_name"),
        capabilities=capabilities_from_twilio(mapping_attr(number, "capabilities")),
        voice_url=string_attr(number, "voice_url"),
        voice_method=string_attr(number, "voice_method"),
        sms_url=string_attr(number, "sms_url"),
        sms_method=string_attr(number, "sms_method"),
        status_callback=string_attr(number, "status_callback"),
        status_callback_method=string_attr(number, "status_callback_method"),
    )


def capabilities_from_twilio(capabilities: dict[str, object]) -> tuple[IntegrationCapability, ...]:
    resolved: list[IntegrationCapability] = []
    if capabilities.get("voice") is True:
        resolved.extend(
            (
                IntegrationCapability.INBOUND_VOICE,
                IntegrationCapability.OUTBOUND_VOICE,
            ),
        )
    if capabilities.get("sms") is True:
        resolved.extend(
            (
                IntegrationCapability.SMS_READ,
                IntegrationCapability.SMS_SEND,
            ),
        )
    return tuple(dict.fromkeys(resolved))


def webhook_update_kwargs(
    *,
    voice_url: str | None,
    voice_method: str | None,
    sms_url: str | None,
    sms_method: str | None,
    status_callback: str | None,
    status_callback_method: str | None,
    clear_missing_urls: bool,
) -> dict[str, object]:
    kwargs: dict[str, object] = {}
    update_url_arg(kwargs, "voice_url", voice_url, clear_missing_urls=clear_missing_urls)
    update_url_arg(kwargs, "sms_url", sms_url, clear_missing_urls=clear_missing_urls)
    update_url_arg(
        kwargs,
        "status_callback",
        status_callback,
        clear_missing_urls=clear_missing_urls,
    )
    update_optional_arg(kwargs, "voice_method", voice_method)
    update_optional_arg(kwargs, "sms_method", sms_method)
    update_optional_arg(kwargs, "status_callback_method", status_callback_method)
    return kwargs


def update_url_arg(
    kwargs: dict[str, object],
    key: str,
    value: str | None,
    *,
    clear_missing_urls: bool,
) -> None:
    if value is not None:
        kwargs[key] = value
    elif clear_missing_urls:
        kwargs[key] = ""


def update_optional_arg(
    kwargs: dict[str, object],
    key: str,
    value: str | None,
) -> None:
    if value is not None:
        kwargs[key] = value


def optional_message_status_callback(status_callback_url: str | None) -> dict[str, object]:
    if status_callback_url is None:
        return {}
    return {"status_callback": status_callback_url}


def optional_call_status_callback(status_callback_url: str | None) -> dict[str, object]:
    if status_callback_url is None:
        return {}
    return {
        "status_callback": status_callback_url,
        "status_callback_method": "POST",
    }


def action_response_for_message(result: object) -> IntegrationActionResponse:
    return IntegrationActionResponse(
        id=required_string_attr(result, "sid"),
        provider=IntegrationProviderType.TWILIO,
        action="send_sms",
        external_id=required_string_attr(result, "sid"),
        status=string_attr(result, "status") or "queued",
        safe_metadata={
            "segments": string_attr(result, "num_segments"),
            "price": string_attr(result, "price"),
            "price_unit": string_attr(result, "price_unit"),
        },
    )


def action_response_for_call(result: object) -> IntegrationActionResponse:
    return IntegrationActionResponse(
        id=required_string_attr(result, "sid"),
        provider=IntegrationProviderType.TWILIO,
        action="place_call",
        external_id=required_string_attr(result, "sid"),
        status=string_attr(result, "status") or "queued",
        safe_metadata={},
    )


def required_string_attr(instance: object, name: str) -> str:
    value = string_attr(instance, name)
    if value is None:
        message = f"Twilio response is missing {name}."
        raise TwilioProviderError(message)
    return value


def string_attr(instance: object, name: str) -> str | None:
    value = getattr(instance, name, None)
    return value if isinstance(value, str) else None


def mapping_attr(instance: object, name: str) -> dict[str, object]:
    value = getattr(instance, name, None)
    return value if isinstance(value, dict) else {}


def twilio_exception_message(exc: TwilioException) -> str:
    message = str(exc).strip()
    return message or TWILIO_API_REQUEST_FAILED_ERROR

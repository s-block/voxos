import binascii
import logging
from collections.abc import Awaitable
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import cast

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel
from telnyx import APIConnectionError, APIError, APIStatusError, AsyncTelnyx, omit

from voxos.agent_runtime.models import JsonObject
from voxos.integrations.models import (
    IntegrationActionResponse,
    IntegrationAuthType,
    IntegrationCapability,
    IntegrationProviderType,
)
from voxos.integrations.providers.base import (
    IntegrationCredentialValidation,
    IntegrationProviderDefinition,
    TelnyxCredentialBundle,
    TelnyxPhoneNumberCleanup,
    TelnyxPhoneNumberConfiguration,
    TelnyxPhoneNumberProvisioning,
)
from voxos.integrations.providers.telnyx.webhooks import (
    TELNYX_PUBLIC_KEY_INVALID_ERROR,
    public_key_bytes,
)

TELNYX_API_BASE_URL = "https://api.telnyx.com/v2"
TELNYX_PROVIDER_DEFINITION = IntegrationProviderDefinition(
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
TELNYX_API_REQUEST_FAILED_ERROR = "Telnyx API request failed."
TELNYX_CONNECTION_NOT_CONFIGURED_ERROR = "Telnyx Voice API application is not configured."
TELNYX_MESSAGING_PROFILE_NOT_CONFIGURED_ERROR = "Telnyx messaging profile is not configured."
TELNYX_PHONE_NUMBER_NOT_FOUND_ERROR = "Telnyx phone number was not found."
TELNYX_PHONE_NUMBER_MISMATCH_ERROR = "Telnyx phone number lookup returned a different number."
TELNYX_PHONE_NUMBER_CAPABILITY_ERROR = (
    "Telnyx phone number must be active and support inbound and outbound voice and SMS."
)
TELNYX_ROUTING_OBJECT_MISSING_ERROR = "A Voxos-owned Telnyx routing object is missing; reconnect the integration."
TELNYX_RESPONSE_DATA_MISSING_ERROR = "Telnyx API response did not include expected data."
TELNYX_HTTP_TIMEOUT_SECONDS = 10.0
TELNYX_OUTBOUND_RING_TIMEOUT_SECONDS = 30

logger = logging.getLogger(__name__)


class TelnyxProviderError(ValueError):
    """Raised when Telnyx rejects or cannot complete a provider operation."""


class TelnyxPhoneNumberCapabilityError(TelnyxProviderError):
    """Raised when a selected number cannot support the required channels."""


@dataclass(frozen=True, slots=True)
class ProvisionedTelnyxIds:
    phone_number_id: str
    call_control_application_id: str | None = None
    messaging_profile_id: str | None = None
    outbound_voice_profile_id: str | None = None
    previous_connection_id: str | None = None
    previous_messaging_profile_id: str | None = None
    voice_number_updated: bool = False
    messaging_number_updated: bool = False


@dataclass(frozen=True, slots=True)
class TelnyxIntegrationProvider:
    api_base_url: str = TELNYX_API_BASE_URL

    def definition(self) -> IntegrationProviderDefinition:
        return TELNYX_PROVIDER_DEFINITION

    async def validate_credentials(
        self,
        credentials: TelnyxCredentialBundle,
    ) -> IntegrationCredentialValidation:
        validate_telnyx_public_key(credentials.webhook_public_key)
        try:
            async with self._client(credentials) as client:
                async for _phone_number in client.phone_numbers.list(
                    handle_messaging_profile_error="true",
                    page_size=1,
                ):
                    break
        except APIError as exc:
            raise TelnyxProviderError(telnyx_error_message(exc)) from exc
        return IntegrationCredentialValidation(
            valid=True,
            status_message=None,
            safe_metadata={
                "webhook_public_key": credentials.webhook_public_key,
                "webhook_public_key_fingerprint": public_key_fingerprint(
                    credentials.webhook_public_key,
                ),
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
        async with self._client(credentials) as client:
            current = await self._phone_number_record(
                client=client,
                phone_number=phone_number,
            )
            phone_number_id = required_string(current, "id")
            messaging_number = await self._messaging_phone_number_record(
                client=client,
                phone_number_id=phone_number_id,
            )
            capabilities = telnyx_number_capabilities(
                number=current,
                messaging_number=messaging_number,
            )
            ensure_required_phone_number_capabilities(capabilities)
            country_destinations = whitelisted_destinations(current)
            resource_name = telnyx_resource_name(label=label, phone_number=phone_number)
            created = ProvisionedTelnyxIds(
                phone_number_id=phone_number_id,
                previous_connection_id=string_value(current, "connection_id"),
                previous_messaging_profile_id=string_value(
                    messaging_number,
                    "messaging_profile_id",
                ),
            )
            try:
                outbound_profile = data_object(
                    await client.outbound_voice_profiles.create(
                        enabled=True,
                        name=resource_name,
                        service_plan="global",
                        traffic_type="conversational",
                        usage_payment_method="rate-deck",
                        whitelisted_destinations=country_destinations,
                    ),
                )
                outbound_profile_id = required_string(outbound_profile, "id")
                created = replace(
                    created,
                    outbound_voice_profile_id=outbound_profile_id,
                )
                call_control_application = data_object(
                    await client.call_control_applications.create(
                        active=True,
                        application_name=resource_name,
                        outbound={"outbound_voice_profile_id": outbound_profile_id},
                        webhook_api_version="2",
                        webhook_event_url=webhook_event_url,
                    ),
                )
                call_control_application_id = required_string(
                    call_control_application,
                    "id",
                )
                created = replace(
                    created,
                    call_control_application_id=call_control_application_id,
                )
                messaging_profile = data_object(
                    await client.messaging_profiles.create(
                        enabled=True,
                        name=resource_name,
                        webhook_api_version="2",
                        webhook_url=webhook_url,
                        whitelisted_destinations=country_destinations,
                    ),
                )
                messaging_profile_id = required_string(messaging_profile, "id")
                created = replace(
                    created,
                    messaging_profile_id=messaging_profile_id,
                )
                updated_number = data_object(
                    await client.phone_numbers.update(
                        phone_number_id,
                        connection_id=call_control_application_id,
                    ),
                )
                created = replace(created, voice_number_updated=True)
                updated_messaging = data_object(
                    await client.phone_numbers.messaging.update(
                        phone_number_id,
                        messaging_profile_id=messaging_profile_id,
                    ),
                )
                created = replace(created, messaging_number_updated=True)
            except (APIError, TelnyxProviderError) as exc:
                await self._cleanup_failed_provisioning(client=client, created=created)
                if isinstance(exc, TelnyxProviderError):
                    raise
                raise TelnyxProviderError(telnyx_error_message(exc)) from exc
        return TelnyxPhoneNumberProvisioning(
            phone_number_id=phone_number_id,
            connection_id=call_control_application_id,
            messaging_profile_id=messaging_profile_id,
            outbound_voice_profile_id=outbound_profile_id,
            capabilities=capabilities,
            previous_number=safe_phone_number_metadata(current),
            number={
                **safe_phone_number_metadata(updated_number),
                "messaging": safe_messaging_phone_number_metadata(updated_messaging),
            },
            voice_application=safe_call_control_application_metadata(
                call_control_application,
            ),
            messaging_profile=safe_messaging_profile_metadata(messaging_profile),
            outbound_voice_profile=safe_outbound_voice_profile_metadata(outbound_profile),
        )

    async def inspect_phone_number_configuration(
        self,
        *,
        credentials: TelnyxCredentialBundle,
        phone_number_id: str,
        outbound_voice_profile_id: str | None,
        expected_voice_url: str,
        expected_sms_url: str,
    ) -> TelnyxPhoneNumberConfiguration:
        try:
            async with self._client(credentials) as client:
                return await self._inspect_phone_number_configuration(
                    client=client,
                    credentials=credentials,
                    phone_number_id=phone_number_id,
                    outbound_voice_profile_id=outbound_voice_profile_id,
                    expected_voice_url=expected_voice_url,
                    expected_sms_url=expected_sms_url,
                )
        except APIError as exc:
            raise TelnyxProviderError(telnyx_error_message(exc)) from exc

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
        connection_id = required_connection_id(credentials)
        messaging_profile_id = required_messaging_profile_id(credentials)
        try:
            async with self._client(credentials) as client:
                number = data_object(await client.phone_numbers.retrieve(phone_number_id))
                messaging_number = data_object(
                    await client.phone_numbers.messaging.retrieve(phone_number_id),
                )
                voice_application = await optional_data_object(
                    client.call_control_applications.retrieve(connection_id),
                )
                messaging_profile = await optional_data_object(
                    client.messaging_profiles.retrieve(messaging_profile_id),
                )
                outbound_voice_profile = await optional_data_object(
                    client.outbound_voice_profiles.retrieve(outbound_voice_profile_id),
                )
                if voice_application is None or messaging_profile is None or outbound_voice_profile is None:
                    raise TelnyxProviderError(TELNYX_ROUTING_OBJECT_MISSING_ERROR)
                resource_name = telnyx_resource_name(
                    label=label,
                    phone_number=string_value(number, "phone_number") or phone_number_id,
                )
                if voice_application_needs_repair(
                    voice_application,
                    expected_voice_url=expected_voice_url,
                    outbound_voice_profile_id=outbound_voice_profile_id,
                ):
                    await client.call_control_applications.update(
                        connection_id,
                        active=True,
                        application_name=(string_value(voice_application, "application_name") or resource_name),
                        outbound={"outbound_voice_profile_id": outbound_voice_profile_id},
                        webhook_api_version="2",
                        webhook_event_url=expected_voice_url,
                    )
                if messaging_profile_needs_repair(
                    messaging_profile,
                    expected_sms_url=expected_sms_url,
                ):
                    await client.messaging_profiles.update(
                        messaging_profile_id,
                        enabled=True,
                        name=string_value(messaging_profile, "name") or resource_name,
                        webhook_api_version="2",
                        webhook_url=expected_sms_url,
                    )
                if outbound_voice_profile.get("enabled") is not True:
                    await client.outbound_voice_profiles.update(
                        outbound_voice_profile_id,
                        enabled=True,
                        name=string_value(outbound_voice_profile, "name") or resource_name,
                    )
                if string_value(number, "connection_id") != connection_id:
                    await client.phone_numbers.update(
                        phone_number_id,
                        connection_id=connection_id,
                    )
                if string_value(messaging_number, "messaging_profile_id") != messaging_profile_id:
                    await client.phone_numbers.messaging.update(
                        phone_number_id,
                        messaging_profile_id=messaging_profile_id,
                    )
                return await self._inspect_phone_number_configuration(
                    client=client,
                    credentials=credentials,
                    phone_number_id=phone_number_id,
                    outbound_voice_profile_id=outbound_voice_profile_id,
                    expected_voice_url=expected_voice_url,
                    expected_sms_url=expected_sms_url,
                )
        except APIError as exc:
            raise TelnyxProviderError(telnyx_error_message(exc)) from exc

    async def release_phone_number(
        self,
        *,
        credentials: TelnyxCredentialBundle,
        cleanup: TelnyxPhoneNumberCleanup,
    ) -> None:
        async with self._client(credentials) as client:
            try:
                if cleanup.messaging_profile_id is not None or cleanup.previous_messaging_profile_id is not None:
                    await client.phone_numbers.messaging.update(
                        cleanup.phone_number_id,
                        messaging_profile_id=cleanup.previous_messaging_profile_id or "",
                    )
                if cleanup.connection_id is not None or cleanup.previous_connection_id is not None:
                    await client.phone_numbers.update(
                        cleanup.phone_number_id,
                        connection_id=cleanup.previous_connection_id or "",
                    )
                if cleanup.connection_id is not None:
                    await required_telnyx_cleanup(
                        "call control application",
                        cleanup.connection_id,
                        client.call_control_applications.delete(cleanup.connection_id),
                        allow_not_found=True,
                    )
                if cleanup.messaging_profile_id is not None:
                    await required_telnyx_cleanup(
                        "messaging profile",
                        cleanup.messaging_profile_id,
                        client.messaging_profiles.delete(cleanup.messaging_profile_id),
                        allow_not_found=True,
                    )
                if cleanup.outbound_voice_profile_id is not None:
                    await required_telnyx_cleanup(
                        "outbound voice profile",
                        cleanup.outbound_voice_profile_id,
                        client.outbound_voice_profiles.delete(cleanup.outbound_voice_profile_id),
                        allow_not_found=True,
                    )
            except APIError as exc:
                raise TelnyxProviderError(telnyx_error_message(exc)) from exc

    async def send_sms(
        self,
        *,
        credentials: TelnyxCredentialBundle,
        from_number: str,
        to_number: str,
        body: str,
        status_callback_url: str | None,
    ) -> IntegrationActionResponse:
        messaging_profile_id = required_messaging_profile_id(credentials)
        try:
            async with self._client(credentials) as client:
                response = await client.messages.send(
                    from_=from_number,
                    messaging_profile_id=messaging_profile_id,
                    text=body,
                    to=to_number,
                    type="SMS",
                    use_profile_webhooks=False if status_callback_url is not None else omit,
                    webhook_url=status_callback_url if status_callback_url is not None else omit,
                )
        except APIError as exc:
            raise TelnyxProviderError(telnyx_error_message(exc)) from exc
        data = data_object(response)
        message_id = required_string(data, "id")
        return IntegrationActionResponse(
            id=message_id,
            provider=IntegrationProviderType.TELNYX,
            action="send_sms",
            external_id=message_id,
            status=message_status(data),
            safe_metadata=safe_message_metadata(data),
        )

    async def send_message(
        self,
        *,
        credentials: TelnyxCredentialBundle,
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
        credentials: TelnyxCredentialBundle,
        from_number: str,
        to_number: str,
        twiml_url: str,
        status_callback_url: str | None,
        command_id: str | None = None,
        client_state: str | None = None,
    ) -> IntegrationActionResponse:
        try:
            async with self._client(credentials) as client:
                response = await client.calls.dial(
                    client_state=client_state if client_state is not None else omit,
                    command_id=command_id if command_id is not None else omit,
                    connection_id=required_connection_id(credentials),
                    from_=from_number,
                    send_silence_when_idle=True,
                    stream_auth_token=credentials.stream_auth_token.get_secret_value(),
                    stream_bidirectional_codec="PCMU",
                    stream_bidirectional_mode="rtp",
                    stream_bidirectional_sampling_rate=8000,
                    stream_bidirectional_target_legs="self",
                    stream_codec="PCMU",
                    stream_establish_before_call_originate=True,
                    stream_track="inbound_track",
                    stream_url=twiml_url,
                    timeout_secs=TELNYX_OUTBOUND_RING_TIMEOUT_SECONDS,
                    to=to_number,
                    webhook_url=status_callback_url if status_callback_url is not None else omit,
                    webhook_url_method="POST",
                )
        except APIError as exc:
            raise TelnyxProviderError(telnyx_error_message(exc)) from exc
        data = data_object(response)
        call_control_id = required_string(data, "call_control_id")
        return IntegrationActionResponse(
            id=string_value(data, "call_leg_id") or string_value(data, "call_session_id") or call_control_id[:36],
            provider=IntegrationProviderType.TELNYX,
            action="place_call",
            external_id=call_control_id,
            status="initiating",
            safe_metadata=safe_call_metadata(data),
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
        try:
            async with self._client(credentials) as client:
                answer = await client.calls.actions.answer(
                    call_control_id,
                    command_id=command_id,
                    send_silence_when_idle=True,
                    webhook_url=webhook_url if webhook_url is not None else omit,
                    webhook_url_method="POST",
                )
                streaming = await client.calls.actions.start_streaming(
                    call_control_id,
                    command_id=f"stream-{command_id[:29]}",
                    stream_auth_token=credentials.stream_auth_token.get_secret_value(),
                    stream_bidirectional_codec="PCMU",
                    stream_bidirectional_mode="rtp",
                    stream_bidirectional_sampling_rate=8000,
                    stream_bidirectional_target_legs="self",
                    stream_codec="PCMU",
                    stream_track="inbound_track",
                    stream_url=stream_url,
                )
        except APIError as exc:
            raise TelnyxProviderError(telnyx_error_message(exc)) from exc
        answer_data = data_object(answer)
        streaming_data = data_object(streaming)
        return IntegrationActionResponse(
            id=command_id[:36],
            provider=IntegrationProviderType.TELNYX,
            action="answer_call",
            external_id=call_control_id,
            status=string_value(streaming_data, "result") or string_value(answer_data, "result") or "accepted",
            safe_metadata={
                "answer": safe_call_metadata(answer_data),
                "streaming": safe_call_metadata(streaming_data),
            },
        )

    async def answer_verification_call(
        self,
        *,
        credentials: TelnyxCredentialBundle,
        call_control_id: str,
        webhook_url: str | None,
        command_id: str,
    ) -> IntegrationActionResponse:
        try:
            async with self._client(credentials) as client:
                answer = await client.calls.actions.answer(
                    call_control_id,
                    command_id=command_id,
                    send_silence_when_idle=True,
                    webhook_url=webhook_url if webhook_url is not None else omit,
                    webhook_url_method="POST",
                )
                transcription = await client.calls.actions.start_transcription(
                    call_control_id,
                    command_id=f"transcribe-{command_id[:24]}",
                    transcription_engine="Telnyx",
                    transcription_tracks="inbound",
                )
        except APIError as exc:
            raise TelnyxProviderError(telnyx_error_message(exc)) from exc
        answer_data = data_object(answer)
        transcription_data = data_object(transcription)
        return IntegrationActionResponse(
            id=command_id[:36],
            provider=IntegrationProviderType.TELNYX,
            action="answer_verification_call",
            external_id=call_control_id,
            status=(string_value(transcription_data, "result") or string_value(answer_data, "result") or "accepted"),
            safe_metadata={
                "answer": safe_call_metadata(answer_data),
                "transcription": safe_call_metadata(transcription_data),
            },
        )

    async def send_call_dtmf(
        self,
        *,
        credentials: TelnyxCredentialBundle,
        call_control_id: str,
        digits: str,
        command_id: str,
    ) -> IntegrationActionResponse:
        try:
            async with self._client(credentials) as client:
                response = await client.calls.actions.send_dtmf(
                    call_control_id,
                    digits=digits,
                    command_id=command_id,
                )
        except APIError as exc:
            raise TelnyxProviderError(telnyx_error_message(exc)) from exc
        data = data_object(response)
        return IntegrationActionResponse(
            id=command_id[:36],
            provider=IntegrationProviderType.TELNYX,
            action="send_call_dtmf",
            external_id=call_control_id,
            status=string_value(data, "result") or "accepted",
            safe_metadata={},
        )

    def _client(self, credentials: TelnyxCredentialBundle) -> AsyncTelnyx:
        return AsyncTelnyx(
            api_key=credentials.api_key.get_secret_value(),
            base_url=self.api_base_url,
            timeout=TELNYX_HTTP_TIMEOUT_SECONDS,
        )

    async def _phone_number_record(
        self,
        *,
        client: AsyncTelnyx,
        phone_number: str,
    ) -> dict[str, object]:
        try:
            async for record_model in client.phone_numbers.list(
                filter={"phone_number": phone_number},
                handle_messaging_profile_error="true",
                page_size=1,
            ):
                record = model_dict(record_model)
                if record.get("phone_number") != phone_number:
                    raise TelnyxProviderError(TELNYX_PHONE_NUMBER_MISMATCH_ERROR)
                return record
        except APIError as exc:
            raise TelnyxProviderError(telnyx_error_message(exc)) from exc
        raise TelnyxProviderError(TELNYX_PHONE_NUMBER_NOT_FOUND_ERROR)

    async def _messaging_phone_number_record(
        self,
        *,
        client: AsyncTelnyx,
        phone_number_id: str,
    ) -> dict[str, object]:
        try:
            return data_object(
                await client.phone_numbers.messaging.retrieve(phone_number_id),
            )
        except APIStatusError as exc:
            if exc.status_code == 404:
                raise TelnyxPhoneNumberCapabilityError(
                    TELNYX_PHONE_NUMBER_CAPABILITY_ERROR,
                ) from exc
            raise TelnyxProviderError(telnyx_error_message(exc)) from exc
        except APIError as exc:
            raise TelnyxProviderError(telnyx_error_message(exc)) from exc

    async def _inspect_phone_number_configuration(
        self,
        *,
        client: AsyncTelnyx,
        credentials: TelnyxCredentialBundle,
        phone_number_id: str,
        outbound_voice_profile_id: str | None,
        expected_voice_url: str,
        expected_sms_url: str,
    ) -> TelnyxPhoneNumberConfiguration:
        number = data_object(await client.phone_numbers.retrieve(phone_number_id))
        messaging_number = await optional_data_object(
            client.phone_numbers.messaging.retrieve(phone_number_id),
        )
        connection_id = credentials.connection_id
        messaging_profile_id = credentials.messaging_profile_id
        voice_application = (
            None
            if connection_id is None
            else await optional_data_object(
                client.call_control_applications.retrieve(connection_id),
            )
        )
        messaging_profile = (
            None
            if messaging_profile_id is None
            else await optional_data_object(
                client.messaging_profiles.retrieve(messaging_profile_id),
            )
        )
        outbound_voice_profile = (
            None
            if outbound_voice_profile_id is None
            else await optional_data_object(
                client.outbound_voice_profiles.retrieve(outbound_voice_profile_id),
            )
        )
        configured_outbound_profile_id = nested_string_value(
            voice_application,
            "outbound",
            "outbound_voice_profile_id",
        )
        return TelnyxPhoneNumberConfiguration(
            phone_number_id=phone_number_id,
            phone_number_status=string_value(number, "status"),
            capabilities=telnyx_number_capabilities(
                number=number,
                messaging_number=messaging_number,
            ),
            connection_id=string_value(number, "connection_id"),
            messaging_profile_id=(
                None if messaging_number is None else string_value(messaging_number, "messaging_profile_id")
            ),
            outbound_voice_profile_id=configured_outbound_profile_id,
            connection_assignment_matches=(
                connection_id is not None and string_value(number, "connection_id") == connection_id
            ),
            messaging_assignment_matches=(
                messaging_profile_id is not None
                and messaging_number is not None
                and string_value(messaging_number, "messaging_profile_id") == messaging_profile_id
            ),
            outbound_voice_profile_assignment_matches=(
                outbound_voice_profile_id is not None and configured_outbound_profile_id == outbound_voice_profile_id
            ),
            voice_application_found=voice_application is not None,
            voice_application_active=(None if voice_application is None else bool_value(voice_application, "active")),
            voice_webhook_matches=(
                voice_application is not None
                and string_value(voice_application, "webhook_event_url") == expected_voice_url
            ),
            voice_webhook_api_version=(
                None if voice_application is None else string_value(voice_application, "webhook_api_version")
            ),
            messaging_profile_found=messaging_profile is not None,
            messaging_profile_enabled=(None if messaging_profile is None else bool_value(messaging_profile, "enabled")),
            messaging_webhook_matches=(
                messaging_profile is not None and string_value(messaging_profile, "webhook_url") == expected_sms_url
            ),
            messaging_webhook_api_version=(
                None if messaging_profile is None else string_value(messaging_profile, "webhook_api_version")
            ),
            outbound_voice_profile_found=outbound_voice_profile is not None,
            outbound_voice_profile_enabled=(
                None if outbound_voice_profile is None else bool_value(outbound_voice_profile, "enabled")
            ),
            messaging_destinations=string_tuple(
                None if messaging_profile is None else messaging_profile.get("whitelisted_destinations"),
            ),
            voice_destinations=string_tuple(
                None if outbound_voice_profile is None else outbound_voice_profile.get("whitelisted_destinations"),
            ),
        )

    async def _cleanup_failed_provisioning(
        self,
        *,
        client: AsyncTelnyx,
        created: ProvisionedTelnyxIds,
    ) -> None:
        if created.messaging_number_updated and created.previous_messaging_profile_id is not None:
            await safe_telnyx_cleanup(
                "phone number messaging settings",
                created.phone_number_id,
                client.phone_numbers.messaging.update(
                    created.phone_number_id,
                    messaging_profile_id=created.previous_messaging_profile_id,
                ),
            )
        if created.voice_number_updated and created.previous_connection_id is not None:
            await safe_telnyx_cleanup(
                "phone number voice settings",
                created.phone_number_id,
                client.phone_numbers.update(
                    created.phone_number_id,
                    connection_id=created.previous_connection_id,
                ),
            )
        if created.call_control_application_id is not None:
            await safe_telnyx_cleanup(
                "call control application",
                created.call_control_application_id,
                client.call_control_applications.delete(created.call_control_application_id),
            )
        if created.messaging_profile_id is not None:
            await safe_telnyx_cleanup(
                "messaging profile",
                created.messaging_profile_id,
                client.messaging_profiles.delete(created.messaging_profile_id),
            )
        if created.outbound_voice_profile_id is not None:
            await safe_telnyx_cleanup(
                "outbound voice profile",
                created.outbound_voice_profile_id,
                client.outbound_voice_profiles.delete(created.outbound_voice_profile_id),
            )


async def safe_telnyx_cleanup(
    resource_type: str,
    resource_id: str,
    operation: Awaitable[object],
) -> None:
    try:
        await operation
    except APIError:
        logger.warning(
            "Failed to clean up Telnyx %s %s after setup failure.",
            resource_type,
            resource_id,
            exc_info=True,
        )


async def required_telnyx_cleanup(
    resource_type: str,
    resource_id: str,
    operation: Awaitable[object],
    *,
    allow_not_found: bool = False,
) -> None:
    try:
        await operation
    except APIStatusError as exc:
        if allow_not_found and exc.status_code == 404:
            return
        raise TelnyxProviderError(telnyx_error_message(exc)) from exc
    except APIError as exc:
        raise TelnyxProviderError(telnyx_error_message(exc)) from exc


async def optional_data_object(operation: Awaitable[object]) -> dict[str, object] | None:
    try:
        return data_object(await operation)
    except APIStatusError as exc:
        if exc.status_code == 404:
            return None
        raise


def data_object(response: object) -> dict[str, object]:
    data = model_dict(response).get("data")
    if not isinstance(data, dict):
        raise TelnyxProviderError(TELNYX_RESPONSE_DATA_MISSING_ERROR)
    return cast(dict[str, object], data)


def model_dict(value: object) -> dict[str, object]:
    if isinstance(value, BaseModel):
        dumped = value.model_dump(mode="json")
        if isinstance(dumped, dict):
            return cast(dict[str, object], dumped)
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    raise TelnyxProviderError(TELNYX_RESPONSE_DATA_MISSING_ERROR)


def telnyx_error_message(exc: APIError) -> str:
    if isinstance(exc, APIStatusError):
        try:
            parsed = exc.response.json()
        except ValueError:
            return f"{TELNYX_API_REQUEST_FAILED_ERROR}: HTTP {exc.status_code}"
        if isinstance(parsed, dict):
            errors = parsed.get("errors")
            if isinstance(errors, list) and errors:
                first = errors[0]
                if isinstance(first, dict):
                    detail = first.get("detail") or first.get("title")
                    if isinstance(detail, str) and detail.strip():
                        return detail.strip()
        return f"{TELNYX_API_REQUEST_FAILED_ERROR}: HTTP {exc.status_code}"
    if isinstance(exc, APIConnectionError):
        return TELNYX_API_REQUEST_FAILED_ERROR
    return str(exc) or TELNYX_API_REQUEST_FAILED_ERROR


def required_string(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TelnyxProviderError(TELNYX_RESPONSE_DATA_MISSING_ERROR)
    return value.strip()


def required_connection_id(credentials: TelnyxCredentialBundle) -> str:
    if credentials.connection_id is None or not credentials.connection_id.strip():
        raise TelnyxProviderError(TELNYX_CONNECTION_NOT_CONFIGURED_ERROR)
    return credentials.connection_id.strip()


def required_messaging_profile_id(credentials: TelnyxCredentialBundle) -> str:
    if credentials.messaging_profile_id is None or not credentials.messaging_profile_id.strip():
        raise TelnyxProviderError(TELNYX_MESSAGING_PROFILE_NOT_CONFIGURED_ERROR)
    return credentials.messaging_profile_id.strip()


def string_value(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def bool_value(data: dict[str, object], key: str) -> bool | None:
    value = data.get(key)
    return value if isinstance(value, bool) else None


def nested_string_value(
    data: dict[str, object] | None,
    parent_key: str,
    child_key: str,
) -> str | None:
    if data is None:
        return None
    nested = data.get(parent_key)
    if not isinstance(nested, dict):
        return None
    return string_value(cast(dict[str, object], nested), child_key)


def string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def telnyx_number_capabilities(
    *,
    number: dict[str, object],
    messaging_number: dict[str, object] | None,
) -> tuple[IntegrationCapability, ...]:
    capabilities: list[IntegrationCapability] = []
    if string_value(number, "status") == "active":
        capabilities.extend(
            (
                IntegrationCapability.INBOUND_VOICE,
                IntegrationCapability.OUTBOUND_VOICE,
                IntegrationCapability.MEDIA_STREAM,
            ),
        )
    sms_features = nested_mapping_value(messaging_number, "features", "sms")
    if sms_features is not None:
        if sms_features.get("domestic_two_way") is True or sms_features.get("international_inbound") is True:
            capabilities.append(IntegrationCapability.SMS_READ)
        if sms_features.get("domestic_two_way") is True or sms_features.get("international_outbound") is True:
            capabilities.append(IntegrationCapability.SMS_SEND)
    if IntegrationCapability.OUTBOUND_VOICE in capabilities or IntegrationCapability.SMS_SEND in capabilities:
        capabilities.append(IntegrationCapability.TOOL_ACTIONS)
    return tuple(capabilities)


def nested_mapping_value(
    data: dict[str, object] | None,
    parent_key: str,
    child_key: str,
) -> dict[str, object] | None:
    if data is None:
        return None
    parent = data.get(parent_key)
    if not isinstance(parent, dict):
        return None
    child = parent.get(child_key)
    if not isinstance(child, dict):
        return None
    return cast(dict[str, object], child)


def ensure_required_phone_number_capabilities(
    capabilities: tuple[IntegrationCapability, ...],
) -> None:
    required = {
        IntegrationCapability.INBOUND_VOICE,
        IntegrationCapability.OUTBOUND_VOICE,
        IntegrationCapability.SMS_READ,
        IntegrationCapability.SMS_SEND,
    }
    if not required.issubset(capabilities):
        raise TelnyxPhoneNumberCapabilityError(TELNYX_PHONE_NUMBER_CAPABILITY_ERROR)


def voice_application_needs_repair(
    application: dict[str, object],
    *,
    expected_voice_url: str,
    outbound_voice_profile_id: str,
) -> bool:
    return (
        application.get("active") is not True
        or string_value(application, "webhook_event_url") != expected_voice_url
        or string_value(application, "webhook_api_version") != "2"
        or nested_string_value(
            application,
            "outbound",
            "outbound_voice_profile_id",
        )
        != outbound_voice_profile_id
    )


def messaging_profile_needs_repair(
    profile: dict[str, object],
    *,
    expected_sms_url: str,
) -> bool:
    return (
        profile.get("enabled") is not True
        or string_value(profile, "webhook_url") != expected_sms_url
        or string_value(profile, "webhook_api_version") != "2"
    )


def public_key_fingerprint(public_key: str) -> str:
    return sha256(public_key.strip().encode("utf-8")).hexdigest()


def validate_telnyx_public_key(public_key: str) -> None:
    try:
        Ed25519PublicKey.from_public_bytes(public_key_bytes(public_key))
    except (binascii.Error, ValueError) as exc:
        raise TelnyxProviderError(TELNYX_PUBLIC_KEY_INVALID_ERROR) from exc


def telnyx_resource_name(*, label: str, phone_number: str) -> str:
    trimmed_label = label.strip() or "Phone"
    last_digits = phone_number[-4:] if len(phone_number) >= 4 else phone_number
    return f"Voxos {trimmed_label} {last_digits}"


def whitelisted_destinations(number: dict[str, object]) -> tuple[str, ...]:
    country = string_value(number, "country_iso_alpha2")
    if country is not None and len(country) == 2 and country.isalpha():
        return (country.upper(),)
    return ("*",)


def safe_call_control_application_metadata(data: dict[str, object]) -> JsonObject:
    return {
        "id": string_value(data, "id"),
        "active": bool_value(data, "active"),
        "application_name": string_value(data, "application_name"),
        "outbound_voice_profile_id": nested_string_value(
            data,
            "outbound",
            "outbound_voice_profile_id",
        ),
        "webhook_event_url": string_value(data, "webhook_event_url"),
        "webhook_event_failover_url": string_value(data, "webhook_event_failover_url"),
        "webhook_api_version": string_value(data, "webhook_api_version"),
    }


def safe_messaging_profile_metadata(data: dict[str, object]) -> JsonObject:
    destinations = data.get("whitelisted_destinations")
    return {
        "id": string_value(data, "id"),
        "enabled": bool_value(data, "enabled"),
        "name": string_value(data, "name"),
        "webhook_url": string_value(data, "webhook_url"),
        "webhook_failover_url": string_value(data, "webhook_failover_url"),
        "webhook_api_version": string_value(data, "webhook_api_version"),
        "whitelisted_destinations": destinations if isinstance(destinations, list) else None,
    }


def safe_outbound_voice_profile_metadata(data: dict[str, object]) -> JsonObject:
    destinations = data.get("whitelisted_destinations")
    return {
        "id": string_value(data, "id"),
        "enabled": bool_value(data, "enabled"),
        "name": string_value(data, "name"),
        "service_plan": string_value(data, "service_plan"),
        "traffic_type": string_value(data, "traffic_type"),
        "usage_payment_method": string_value(data, "usage_payment_method"),
        "whitelisted_destinations": destinations if isinstance(destinations, list) else None,
    }


def safe_phone_number_metadata(data: dict[str, object]) -> JsonObject:
    return {
        "id": string_value(data, "id"),
        "phone_number": string_value(data, "phone_number"),
        "status": string_value(data, "status"),
        "country_iso_alpha2": string_value(data, "country_iso_alpha2"),
        "connection_id": string_value(data, "connection_id"),
        "connection_name": string_value(data, "connection_name"),
        "messaging_profile_id": string_value(data, "messaging_profile_id"),
        "messaging_profile_name": string_value(data, "messaging_profile_name"),
    }


def safe_messaging_phone_number_metadata(data: dict[str, object]) -> JsonObject:
    return {
        "id": string_value(data, "id"),
        "phone_number": string_value(data, "phone_number"),
        "messaging_profile_id": string_value(data, "messaging_profile_id"),
        "messaging_product": string_value(data, "messaging_product"),
    }


def safe_message_metadata(data: dict[str, object]) -> JsonObject:
    cost = data.get("cost")
    return {
        "id": string_value(data, "id"),
        "direction": string_value(data, "direction"),
        "messaging_profile_id": string_value(data, "messaging_profile_id"),
        "parts": data.get("parts") if isinstance(data.get("parts"), int) else None,
        "type": string_value(data, "type"),
        "cost": cost if isinstance(cost, dict) else None,
    }


def safe_call_metadata(data: dict[str, object]) -> JsonObject:
    return {
        "call_control_id": string_value(data, "call_control_id"),
        "call_leg_id": string_value(data, "call_leg_id"),
        "call_session_id": string_value(data, "call_session_id"),
        "result": string_value(data, "result"),
    }


def message_status(data: dict[str, object]) -> str:
    to_values = data.get("to")
    if isinstance(to_values, list) and to_values:
        first = to_values[0]
        if isinstance(first, dict):
            status = first.get("status")
            if isinstance(status, str) and status.strip():
                return status.strip()
    return string_value(data, "status") or "queued"

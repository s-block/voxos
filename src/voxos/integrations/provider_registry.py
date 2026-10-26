from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from voxos.integrations.models import IntegrationProviderType
from voxos.integrations.providers.base import (
    TelnyxProviderCapabilities,
    TwilioProviderCapabilities,
)

PROVIDER_NOT_REGISTERED_ERROR = "Integration provider is not registered."


@dataclass(frozen=True, slots=True, init=False)
class IntegrationProviderRegistry:
    providers: Mapping[
        IntegrationProviderType,
        TwilioProviderCapabilities | TelnyxProviderCapabilities,
    ]

    def __init__(
        self,
        providers: Mapping[
            IntegrationProviderType,
            TwilioProviderCapabilities | TelnyxProviderCapabilities,
        ]
        | None = None,
        *,
        telnyx: TelnyxProviderCapabilities | None = None,
        twilio: TwilioProviderCapabilities | None = None,
    ) -> None:
        resolved: dict[
            IntegrationProviderType,
            TwilioProviderCapabilities | TelnyxProviderCapabilities,
        ] = {}
        if providers is not None:
            resolved.update(providers)
        if telnyx is not None:
            resolved[IntegrationProviderType.TELNYX] = telnyx
        if twilio is not None:
            resolved[IntegrationProviderType.TWILIO] = twilio
        object.__setattr__(self, "providers", resolved)

    def provider_for(
        self,
        provider_type: IntegrationProviderType,
    ) -> TwilioProviderCapabilities | TelnyxProviderCapabilities:
        try:
            return self.providers[provider_type]
        except KeyError as exc:
            message = f"{PROVIDER_NOT_REGISTERED_ERROR}: {provider_type.value}"
            raise ValueError(message) from exc

    def number_provider_for(
        self,
        provider_type: IntegrationProviderType,
    ) -> TwilioProviderCapabilities:
        return self.twilio_provider_for(provider_type)

    def sms_provider_for(
        self,
        provider_type: IntegrationProviderType,
    ) -> TwilioProviderCapabilities | TelnyxProviderCapabilities:
        return self.provider_for(provider_type)

    def voice_provider_for(
        self,
        provider_type: IntegrationProviderType,
    ) -> TwilioProviderCapabilities | TelnyxProviderCapabilities:
        return self.provider_for(provider_type)

    def phone_number_webhook_provider_for(
        self,
        provider_type: IntegrationProviderType,
    ) -> TwilioProviderCapabilities:
        return self.twilio_provider_for(provider_type)

    def twilio_provider_for(
        self,
        provider_type: IntegrationProviderType = IntegrationProviderType.TWILIO,
    ) -> TwilioProviderCapabilities:
        provider = self.provider_for(provider_type)
        if provider_type != IntegrationProviderType.TWILIO:
            message = f"{PROVIDER_NOT_REGISTERED_ERROR}: {provider_type.value}"
            raise ValueError(message)
        return cast(TwilioProviderCapabilities, provider)

    def telnyx_provider_for(self) -> TelnyxProviderCapabilities:
        provider = self.provider_for(IntegrationProviderType.TELNYX)
        return cast(TelnyxProviderCapabilities, provider)

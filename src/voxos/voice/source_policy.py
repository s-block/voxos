from enum import StrEnum


class VoiceSessionSource(StrEnum):
    LIVEKIT_API = "livekit_api"
    TELNYX = "telnyx"
    TWILIO = "twilio"


class VoiceSessionKind(StrEnum):
    LIVEKIT = "livekit"
    PROVIDER_BRIDGED_PHONE = "provider_bridged_phone"


class VoiceSessionSourcePolicy:
    _PHONE_SOURCES = frozenset(
        {
            VoiceSessionSource.TELNYX.value,
            VoiceSessionSource.TWILIO.value,
        },
    )

    @classmethod
    def classify(cls, source: str) -> VoiceSessionKind:
        if source in cls._PHONE_SOURCES:
            return VoiceSessionKind.PROVIDER_BRIDGED_PHONE
        return VoiceSessionKind.LIVEKIT

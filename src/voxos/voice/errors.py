from typing import Self

from voxos.providers.models import RealtimeVoiceProvider


class VoiceAgentError(RuntimeError):
    """Base error for voice agent setup and runtime failures."""


class UnsupportedVoiceProviderError(VoiceAgentError):
    def __init__(self, provider: RealtimeVoiceProvider | str) -> None:
        super().__init__(f"Unsupported realtime voice provider: {provider}")
        self.provider = provider


class VoicePreferencesNotFoundError(VoiceAgentError):
    def __init__(self, user_id: str) -> None:
        super().__init__(f"Voice preferences not found for user: {user_id}")
        self.user_id = user_id


class MissingVoiceCredentialError(VoiceAgentError):
    def __init__(self, credential_ref: str) -> None:
        super().__init__(f"Voice credential is not configured for ref: {credential_ref}")
        self.credential_ref = credential_ref


class InvalidVoiceConfigurationError(VoiceAgentError):
    @classmethod
    def invalid_preferences(cls, user_id: str) -> Self:
        return cls(f"Invalid voice preferences for user: {user_id}")

    @classmethod
    def provider_mismatch(cls) -> Self:
        return cls("Resolved voice credentials do not match selected provider")


class VoiceProviderConnectionError(VoiceAgentError):
    @classmethod
    def openai_start_failed(cls) -> Self:
        return cls("OpenAI realtime LiveKit session failed to start")

    @classmethod
    def worker_failed(cls) -> Self:
        return cls("LiveKit voice worker failed")


class MissingVoiceRouteMetadataError(VoiceAgentError):
    def __init__(self, metadata_keys: tuple[str, ...]) -> None:
        super().__init__(
            f"LiveKit job metadata does not include route keys: {', '.join(metadata_keys)}",
        )
        self.metadata_keys = metadata_keys


class MissingVoiceRouteSignerError(VoiceAgentError):
    def __init__(self) -> None:
        super().__init__("LiveKit route signing is not configured")


class InvalidVoiceRouteSignatureError(VoiceAgentError):
    def __init__(self) -> None:
        super().__init__("LiveKit route metadata signature is invalid")


class MissingLiveKitCredentialsError(VoiceAgentError):
    def __init__(self) -> None:
        super().__init__("LiveKit API key and secret are required to mint voice tokens")


class InvalidLiveKitCredentialsError(VoiceAgentError):
    def __init__(self) -> None:
        super().__init__("Saved LiveKit credentials could not be resolved")


class DisallowedLiveKitEndpointError(VoiceAgentError):
    def __init__(self) -> None:
        super().__init__("Saved LiveKit endpoint is not allowed by worker policy")


class VoiceWorkerUnavailableError(VoiceAgentError):
    def __init__(self, reason_code: str = "worker_unavailable") -> None:
        super().__init__("LiveKit voice capacity is temporarily unavailable")
        self.reason_code = reason_code


class VoiceWorkerRegistrationError(VoiceAgentError):
    def __init__(self, reason_code: str = "registration_failed") -> None:
        super().__init__("LiveKit project worker registration failed")
        self.reason_code = reason_code

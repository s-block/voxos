from dataclasses import dataclass
from typing import Protocol

from voxos.providers.models import RealtimeVoiceProvider
from voxos.voice.models import ResolvedVoiceCredentials, UserVoicePreferences
from voxos.voice.tools import VoiceToolProvider


class RealtimeSessionObserver(Protocol):
    def register(self, session: object) -> None:
        """Register callbacks on a provider-created LiveKit session."""


@dataclass(frozen=True, slots=True)
class RealtimeVoiceSessionOptions:
    user_away_timeout_seconds: float | None = 30.0
    delete_room_on_close: bool = True
    telephony: bool = False


DEFAULT_REALTIME_SESSION_OPTIONS = RealtimeVoiceSessionOptions()


class RealtimeSpeechHandle(Protocol):
    @property
    def interrupted(self) -> bool:
        """Whether playout was interrupted."""

    async def wait_for_playout(self) -> None:
        """Wait until generated speech finishes or is interrupted."""


class RealtimeVoiceSession(Protocol):
    async def start(
        self,
        room: object,
        *,
        participant_identity: str | None = None,
        audio_input_enabled: bool = True,
    ) -> None:
        """Initialize the provider session without generating speech."""

    def generate_initial_reply(
        self,
        *,
        instructions: str | None = None,
    ) -> RealtimeSpeechHandle | None:
        """Schedule the configured or overridden first reply."""

    async def wait_for_media_ready(self) -> None:
        """Wait until the expected room participant and audio are ready."""

    async def wait_for_first_speech(self) -> None:
        """Wait until the provider reports that the agent started speaking."""

    def set_audio_input_enabled(self, enabled: bool) -> None:
        """Enable or disable provider audio input."""


class RealtimeVoiceSessionFactory(Protocol):
    @property
    def provider(self) -> RealtimeVoiceProvider:
        """Provider handled by this session factory."""

    async def create_session(
        self,
        preferences: UserVoicePreferences,
        credentials: ResolvedVoiceCredentials,
        tool_provider: VoiceToolProvider,
        session_options: RealtimeVoiceSessionOptions = DEFAULT_REALTIME_SESSION_OPTIONS,
        session_observer: RealtimeSessionObserver | None = None,
    ) -> RealtimeVoiceSession:
        """Create a LiveKit realtime session for a user's voice preferences."""

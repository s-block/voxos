import asyncio
import logging
from dataclasses import dataclass, field
from typing import cast

from livekit import rtc
from livekit.agents import (
    Agent,
    AgentSession,
    AgentStateChangedEvent,
    UserStateChangedEvent,
    room_io,
)
from livekit.agents.types import NOT_GIVEN
from livekit.plugins import google

from voxos.agent_core.response_profiles import CommunicationMedium, response_profile
from voxos.providers.base import (
    DEFAULT_REALTIME_SESSION_OPTIONS,
    RealtimeSessionObserver,
    RealtimeSpeechHandle,
    RealtimeVoiceSession,
    RealtimeVoiceSessionOptions,
)
from voxos.providers.models import RealtimeVoiceConfig, RealtimeVoiceProvider
from voxos.providers.openai_realtime import (
    initial_reply_instructions,
    language_preference_instruction,
)
from voxos.providers.turn_detection import gemini_realtime_input_config
from voxos.voice.errors import VoiceProviderConnectionError
from voxos.voice.models import ResolvedVoiceCredentials, UserVoicePreferences
from voxos.voice.tools import VoiceToolProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GoogleGeminiLiveSession:
    session: AgentSession
    agent: Agent
    initial_reply_instructions: str | None
    delete_room_on_close: bool
    session_observer: RealtimeSessionObserver | None = None
    first_speech_event: asyncio.Event = field(
        default_factory=asyncio.Event,
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.session_observer is not None:
            self.session_observer.register(self.session)
        self.session.on("user_state_changed", self._handle_user_state_changed)
        self.session.on("agent_state_changed", self._handle_agent_state_changed)

    async def start(
        self,
        room: object,
        *,
        participant_identity: str | None = None,
        audio_input_enabled: bool = True,
    ) -> None:
        try:
            if not audio_input_enabled:
                self.session.input.set_audio_enabled(False)
            await self.session.start(
                agent=self.agent,
                room=cast(rtc.Room, room),
                room_options=room_io.RoomOptions(
                    participant_identity=(participant_identity if participant_identity is not None else NOT_GIVEN),
                    close_on_disconnect=True,
                    delete_room_on_close=self.delete_room_on_close,
                ),
            )
        except Exception as exc:
            logger.exception("Google Gemini LiveKit session failed to start")
            raise VoiceProviderConnectionError.worker_failed() from exc

    def generate_initial_reply(
        self,
        *,
        instructions: str | None = None,
    ) -> RealtimeSpeechHandle | None:
        resolved_instructions = instructions if instructions is not None else self.initial_reply_instructions
        if resolved_instructions is None:
            return None
        return self.session.generate_reply(
            instructions=resolved_instructions,
            input_modality="audio",
            allow_interruptions=True,
        )

    async def wait_for_media_ready(self) -> None:
        await self.session.room_io.wait_for_ready()

    async def wait_for_first_speech(self) -> None:
        await self.first_speech_event.wait()

    def set_audio_input_enabled(self, enabled: bool) -> None:
        self.session.input.set_audio_enabled(enabled)

    def _handle_agent_state_changed(self, event: AgentStateChangedEvent) -> None:
        if event.new_state == "speaking":
            self.first_speech_event.set()

    def _handle_user_state_changed(self, event: UserStateChangedEvent) -> None:
        if event.new_state != "away":
            return

        try:
            logger.info(
                "Shutting down inactive Gemini LiveKit voice session",
                extra={
                    "old_state": event.old_state,
                    "new_state": event.new_state,
                },
            )
            self.session.shutdown(drain=False)
        except Exception:
            logger.exception("Failed to shut down inactive Gemini LiveKit session")


class GoogleGeminiLiveSessionFactory:
    @property
    def provider(self) -> RealtimeVoiceProvider:
        return RealtimeVoiceProvider.GOOGLE_GEMINI_LIVE

    async def create_session(
        self,
        preferences: UserVoicePreferences,
        credentials: ResolvedVoiceCredentials,
        tool_provider: VoiceToolProvider,
        session_options: RealtimeVoiceSessionOptions = DEFAULT_REALTIME_SESSION_OPTIONS,
        session_observer: RealtimeSessionObserver | None = None,
    ) -> RealtimeVoiceSession:
        config = preferences.provider_config
        tools = list(await tool_provider.get_tools(preferences.user_id))
        realtime_model = self._create_realtime_model(
            preferences=preferences,
            credentials=credentials,
            config=config,
            session_options=session_options,
        )
        return GoogleGeminiLiveSession(
            session=AgentSession(
                llm=realtime_model,
                user_away_timeout=session_options.user_away_timeout_seconds,
            ),
            agent=Agent(instructions=agent_instructions(config), tools=tools),
            initial_reply_instructions=initial_reply_instructions(config),
            delete_room_on_close=session_options.delete_room_on_close,
            session_observer=session_observer,
        )

    def _create_realtime_model(
        self,
        preferences: UserVoicePreferences,
        credentials: ResolvedVoiceCredentials,
        config: RealtimeVoiceConfig,
        session_options: RealtimeVoiceSessionOptions,
    ) -> google.realtime.RealtimeModel:
        return google.realtime.RealtimeModel(
            model=preferences.model,
            voice=preferences.voice,
            api_key=credentials.token.get_secret_value(),
            instructions=agent_instructions(config),
            max_output_tokens=cast(
                int,
                response_profile(CommunicationMedium.VOICE).max_output_tokens,
            ),
            realtime_input_config=gemini_realtime_input_config(session_options),
        )


def create_google_gemini_live_session_factory() -> GoogleGeminiLiveSessionFactory:
    return GoogleGeminiLiveSessionFactory()


def agent_instructions(config: RealtimeVoiceConfig) -> str:
    return "\n\n".join(
        (
            config.agent_instructions.strip(),
            language_preference_instruction(config.preferred_language),
        ),
    )

from collections.abc import Callable
from typing import cast

import pytest
from livekit.agents import Agent, AgentSession

from voxos.providers.google_gemini_live import GoogleGeminiLiveSession
from voxos.providers.grok_voice_agent import GrokVoiceAgentSession
from voxos.providers.openai_realtime import OpenAIRealtimeSession

EventCallback = Callable[[object], None]


class FakeAudioInput:
    def __init__(self) -> None:
        self.enabled_values: list[bool] = []

    def set_audio_enabled(self, enabled: bool) -> None:
        self.enabled_values.append(enabled)


class FakeRoomIO:
    def __init__(self) -> None:
        self.wait_count = 0

    async def wait_for_ready(self) -> None:
        self.wait_count += 1


class FakeSpeechHandle:
    interrupted = False

    async def wait_for_playout(self) -> None:
        return None


class FakeAgentSession:
    def __init__(self) -> None:
        self.input = FakeAudioInput()
        self.room_io = FakeRoomIO()
        self.event_callbacks: dict[str, EventCallback] = {}
        self.reply_kwargs: dict[str, object] | None = None
        self.room_options: object | None = None

    def on(self, event: str, callback: EventCallback) -> EventCallback:
        self.event_callbacks[event] = callback
        return callback

    async def start(
        self,
        *,
        agent: object,
        room: object,
        room_options: object,
    ) -> None:
        del agent, room
        self.room_options = room_options

    def generate_reply(self, **kwargs: object) -> FakeSpeechHandle:
        self.reply_kwargs = kwargs
        return FakeSpeechHandle()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session_class",
    (
        OpenAIRealtimeSession,
        GoogleGeminiLiveSession,
        GrokVoiceAgentSession,
    ),
)
async def test_realtime_provider_start_is_silent_until_explicit_opening(
    session_class: type[OpenAIRealtimeSession | GoogleGeminiLiveSession | GrokVoiceAgentSession],
) -> None:
    fake_session = FakeAgentSession()
    provider_session = session_class(
        session=cast(AgentSession, fake_session),
        agent=cast(Agent, object()),
        initial_reply_instructions="Say the configured greeting.",
        delete_room_on_close=True,
    )

    await provider_session.start(
        object(),
        participant_identity="call-session-123",
        audio_input_enabled=False,
    )

    assert fake_session.input.enabled_values == [False]
    assert fake_session.reply_kwargs is None
    assert fake_session.room_options is not None
    assert fake_session.room_options.participant_identity == "call-session-123"

    speech_handle = provider_session.generate_initial_reply(
        instructions="Begin the persisted call objective.",
    )
    provider_session.set_audio_input_enabled(True)
    await provider_session.wait_for_media_ready()

    assert isinstance(speech_handle, FakeSpeechHandle)
    assert fake_session.reply_kwargs == {
        "instructions": "Begin the persisted call objective.",
        "input_modality": "audio",
        "allow_interruptions": True,
    }
    assert fake_session.input.enabled_values == [False, True]
    assert fake_session.room_io.wait_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session_class",
    (
        OpenAIRealtimeSession,
        GoogleGeminiLiveSession,
        GrokVoiceAgentSession,
    ),
)
async def test_realtime_provider_uses_default_enabled_audio_input(
    session_class: type[OpenAIRealtimeSession | GoogleGeminiLiveSession | GrokVoiceAgentSession],
) -> None:
    fake_session = FakeAgentSession()
    provider_session = session_class(
        session=cast(AgentSession, fake_session),
        agent=cast(Agent, object()),
        initial_reply_instructions=None,
        delete_room_on_close=True,
    )

    await provider_session.start(object(), audio_input_enabled=True)

    assert fake_session.input.enabled_values == []

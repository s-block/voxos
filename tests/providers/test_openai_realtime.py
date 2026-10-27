from collections.abc import Callable, Sequence
from typing import ClassVar, cast

import pytest
from livekit.agents import AgentStateChangedEvent, UserStateChangedEvent
from pydantic import SecretStr

from voxos.providers.base import RealtimeVoiceSessionOptions
from voxos.providers.models import OpenAIRealtimeConfig, RealtimeVoiceProvider
from voxos.providers.openai_realtime import (
    OpenAIRealtimeSession,
    OpenAIRealtimeSessionFactory,
)
from voxos.voice.models import ResolvedVoiceCredentials, UserVoicePreferences
from voxos.voice.tools import VoiceLiveKitTool

EventCallback = Callable[[object], None]
EventDecorator = Callable[[EventCallback], EventCallback]


class FakeRealtimeModel:
    instances: ClassVar[list["FakeRealtimeModel"]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.instances.append(self)


class FakeAgentSession:
    def __init__(
        self,
        *,
        llm: object,
        user_away_timeout: float | None,
    ) -> None:
        self.llm = llm
        self.user_away_timeout = user_away_timeout
        self.started = False
        self.room_options: object | None = None
        self.reply_kwargs: dict[str, object] | None = None
        self.shutdown_kwargs: dict[str, object] | None = None
        self.event_callbacks: dict[str, EventCallback] = {}
        self.input = FakeAudioInput()
        self.room_io = FakeRoomIO()

    async def start(
        self,
        agent: object,
        *,
        room: object,
        room_options: object,
    ) -> None:
        self.started = True
        self.agent = agent
        self.room = room
        self.room_options = room_options

    def generate_reply(self, **kwargs: object) -> "FakeSpeechHandle":
        self.reply_kwargs = kwargs
        return FakeSpeechHandle()

    def on(
        self,
        event: str,
        callback: EventCallback | None = None,
    ) -> EventCallback | EventDecorator:
        if callback is not None:
            self.event_callbacks[event] = callback
            return callback

        def decorator(inner_callback: EventCallback) -> EventCallback:
            self.event_callbacks[event] = inner_callback
            return inner_callback

        return decorator

    def shutdown(self, **kwargs: object) -> None:
        self.shutdown_kwargs = kwargs


class FakeAudioInput:
    def __init__(self) -> None:
        self.enabled_values: list[bool] = []

    def set_audio_enabled(self, enabled: bool) -> None:
        self.enabled_values.append(enabled)


class FakeRoomIO:
    async def wait_for_ready(self) -> None:
        return None


class FakeSpeechHandle:
    interrupted = False

    async def wait_for_playout(self) -> None:
        return None


class FakeAgent:
    def __init__(
        self,
        *,
        instructions: str,
        tools: Sequence[VoiceLiveKitTool] | None = None,
    ) -> None:
        self.instructions = instructions
        self.tools = tools


class EmptyToolProvider:
    async def get_tools(self, user_id: str) -> Sequence[VoiceLiveKitTool]:
        return ()


@pytest.mark.asyncio
async def test_openai_factory_builds_session_without_logging_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voxos.providers import openai_realtime

    FakeRealtimeModel.instances.clear()
    monkeypatch.setattr(openai_realtime.openai.realtime, "RealtimeModel", FakeRealtimeModel)
    monkeypatch.setattr(openai_realtime, "AgentSession", FakeAgentSession)
    monkeypatch.setattr(openai_realtime, "Agent", FakeAgent)

    preferences = UserVoicePreferences(
        user_id="user-123",
        provider=RealtimeVoiceProvider.OPENAI_REALTIME,
        model="gpt-realtime",
        voice="marin",
        credential_ref="openai-local",
        provider_config=OpenAIRealtimeConfig(
            modalities=("audio",),
            base_url="https://api.example.test/v1",
            agent_instructions="Be concise.",
            preferred_language="English",
            initial_reply_instructions="Say hello.",
        ),
    )
    credentials = ResolvedVoiceCredentials(
        provider=RealtimeVoiceProvider.OPENAI_REALTIME,
        reference="openai-local",
        token=SecretStr("sk-test-secret"),
    )

    session = await OpenAIRealtimeSessionFactory().create_session(
        preferences=preferences,
        credentials=credentials,
        tool_provider=EmptyToolProvider(),
        session_options=RealtimeVoiceSessionOptions(
            user_away_timeout_seconds=30.0,
            delete_room_on_close=True,
        ),
    )

    openai_session = cast(OpenAIRealtimeSession, session)
    assert isinstance(openai_session.session, FakeAgentSession)
    assert isinstance(openai_session.agent, FakeAgent)
    assert cast(FakeAgent, openai_session.agent).instructions == (
        "Be concise.\n\n"
        "Language preference: Start every voice session in English. "
        "Respond in English by default unless the user explicitly asks for a different language."
    )
    assert "sk-test-secret" not in str(credentials)

    fake_session = cast(FakeAgentSession, openai_session.session)
    assert fake_session.user_away_timeout == 30.0
    assert "user_state_changed" in fake_session.event_callbacks
    assert "agent_state_changed" in fake_session.event_callbacks

    model_kwargs = FakeRealtimeModel.instances[0].kwargs
    assert model_kwargs["model"] == "gpt-realtime"
    assert model_kwargs["voice"] == "marin"
    assert model_kwargs["modalities"] == ["audio"]
    assert model_kwargs["base_url"] == "https://api.example.test/v1"
    assert model_kwargs["api_key"] == "sk-test-secret"

    await openai_session.start(room=object())

    assert fake_session.started is True
    assert fake_session.room_options is not None
    assert fake_session.room_options.close_on_disconnect is True
    assert fake_session.room_options.delete_room_on_close is True
    assert fake_session.input.enabled_values == []
    assert fake_session.reply_kwargs is None

    speech_handle = openai_session.generate_initial_reply()

    assert isinstance(speech_handle, FakeSpeechHandle)
    assert fake_session.reply_kwargs is not None
    assert fake_session.reply_kwargs["input_modality"] == "audio"
    reply_instructions = fake_session.reply_kwargs["instructions"]
    assert isinstance(reply_instructions, str)
    assert "Say hello." in reply_instructions
    assert "Use English for this first spoken reply." in reply_instructions
    assert "30 to 50 spoken words" in reply_instructions

    fake_session.event_callbacks["agent_state_changed"](
        AgentStateChangedEvent(old_state="listening", new_state="speaking"),
    )
    await openai_session.wait_for_first_speech()

    fake_session.event_callbacks["user_state_changed"](
        UserStateChangedEvent(old_state="listening", new_state="away"),
    )

    assert fake_session.shutdown_kwargs == {"drain": False}

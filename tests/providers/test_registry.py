from dataclasses import dataclass

import pytest

from voxos.providers.base import RealtimeVoiceSession, RealtimeVoiceSessionOptions
from voxos.providers.models import RealtimeVoiceProvider
from voxos.providers.registry import VoiceProviderRegistry
from voxos.voice.errors import UnsupportedVoiceProviderError
from voxos.voice.models import ResolvedVoiceCredentials, UserVoicePreferences
from voxos.voice.tools import VoiceToolProvider


@dataclass(frozen=True, slots=True)
class FakeSession:
    async def start(self, room: object) -> None:
        return None


class FakeFactory:
    @property
    def provider(self) -> RealtimeVoiceProvider:
        return RealtimeVoiceProvider.OPENAI_REALTIME

    async def create_session(
        self,
        preferences: UserVoicePreferences,
        credentials: ResolvedVoiceCredentials,
        tool_provider: VoiceToolProvider,
        session_options: RealtimeVoiceSessionOptions,
    ) -> RealtimeVoiceSession:
        return FakeSession()


def test_get_factory_returns_registered_provider() -> None:
    registry = VoiceProviderRegistry(factories=(FakeFactory(),))

    factory = registry.get_factory(RealtimeVoiceProvider.OPENAI_REALTIME)

    assert factory.provider == RealtimeVoiceProvider.OPENAI_REALTIME


def test_get_factory_rejects_unknown_provider_string() -> None:
    registry = VoiceProviderRegistry(factories=(FakeFactory(),))

    with pytest.raises(UnsupportedVoiceProviderError):
        registry.get_factory("gemini_live")

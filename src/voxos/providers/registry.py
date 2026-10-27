from collections.abc import Iterable

from voxos.providers.base import (
    DEFAULT_REALTIME_SESSION_OPTIONS,
    RealtimeSessionObserver,
    RealtimeVoiceSession,
    RealtimeVoiceSessionFactory,
    RealtimeVoiceSessionOptions,
)
from voxos.providers.models import RealtimeVoiceProvider
from voxos.voice.errors import UnsupportedVoiceProviderError
from voxos.voice.models import ResolvedVoiceCredentials, UserVoicePreferences
from voxos.voice.tools import VoiceToolProvider


class VoiceProviderRegistry:
    def __init__(self, factories: Iterable[RealtimeVoiceSessionFactory]) -> None:
        self._factories = {factory.provider: factory for factory in factories}

    async def create_session(
        self,
        preferences: UserVoicePreferences,
        credentials: ResolvedVoiceCredentials,
        tool_provider: VoiceToolProvider,
        session_options: RealtimeVoiceSessionOptions = DEFAULT_REALTIME_SESSION_OPTIONS,
        session_observer: RealtimeSessionObserver | None = None,
    ) -> RealtimeVoiceSession:
        factory = self.get_factory(preferences.provider)
        if session_observer is None:
            return await factory.create_session(
                preferences,
                credentials,
                tool_provider,
                session_options,
            )
        return await factory.create_session(
            preferences,
            credentials,
            tool_provider,
            session_options,
            session_observer,
        )

    def get_factory(
        self,
        provider: RealtimeVoiceProvider | str,
    ) -> RealtimeVoiceSessionFactory:
        realtime_provider = self._coerce_provider(provider)
        factory = self._factories.get(realtime_provider)
        if factory is None:
            raise UnsupportedVoiceProviderError(realtime_provider)
        return factory

    def _coerce_provider(
        self,
        provider: RealtimeVoiceProvider | str,
    ) -> RealtimeVoiceProvider:
        if isinstance(provider, RealtimeVoiceProvider):
            return provider

        try:
            return RealtimeVoiceProvider(provider)
        except ValueError as exc:
            raise UnsupportedVoiceProviderError(provider) from exc

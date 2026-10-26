from voxos.providers.google_gemini_live import (
    create_google_gemini_live_session_factory,
)
from voxos.providers.grok_voice_agent import create_grok_voice_agent_session_factory
from voxos.providers.openai_realtime import create_openai_realtime_session_factory
from voxos.providers.registry import VoiceProviderRegistry


def default_voice_provider_registry() -> VoiceProviderRegistry:
    return VoiceProviderRegistry(
        factories=(
            create_openai_realtime_session_factory(),
            create_google_gemini_live_session_factory(),
            create_grok_voice_agent_session_factory(),
        ),
    )

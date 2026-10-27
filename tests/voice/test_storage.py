import pytest

from voxos.db.session import AsyncSessionFactory
from voxos.providers.models import OpenAIRealtimeConfig
from voxos.user_settings.services import UserVoicePreferenceService
from voxos.voice.errors import VoicePreferencesNotFoundError
from voxos.voice.models import UserVoicePreferences
from voxos.voice.storage import SqlModelVoicePreferenceStore


@pytest.mark.asyncio
async def test_sqlmodel_store_loads_preferences(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    preferences = UserVoicePreferences(
        user_id="user-123",
        model="gpt-realtime",
        voice="marin",
        credential_ref="credential-123",
        provider_config=OpenAIRealtimeConfig(agent_instructions="Be helpful."),
    )
    async with sqlite_session_factory() as session:
        await UserVoicePreferenceService(session).set_preferences(preferences)

    store = SqlModelVoicePreferenceStore(session_factory=sqlite_session_factory)

    assert await store.get_preferences("user-123") == preferences


@pytest.mark.asyncio
async def test_sqlmodel_store_raises_for_missing_preferences(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    store = SqlModelVoicePreferenceStore(session_factory=sqlite_session_factory)

    with pytest.raises(VoicePreferencesNotFoundError):
        await store.get_preferences("user-123")

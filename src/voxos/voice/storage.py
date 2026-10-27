from dataclasses import dataclass
from typing import Protocol

from voxos.db.session import AsyncSessionFactory
from voxos.user_settings.services import UserVoicePreferenceService
from voxos.voice.models import UserVoicePreferences


class VoicePreferenceStore(Protocol):
    async def get_preferences(self, user_id: str) -> UserVoicePreferences:
        """Load non-secret voice preferences for a user."""


@dataclass(frozen=True, slots=True)
class SqlModelVoicePreferenceStore:
    session_factory: AsyncSessionFactory

    async def get_preferences(self, user_id: str) -> UserVoicePreferences:
        async with self.session_factory() as session:
            service = UserVoicePreferenceService(session)
            return await service.get_preferences(user_id)

    async def set_preferences(
        self,
        preferences: UserVoicePreferences,
    ) -> object:
        async with self.session_factory() as session:
            service = UserVoicePreferenceService(session)
            return await service.set_preferences(preferences)

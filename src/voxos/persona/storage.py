from typing import Protocol

from voxos.persona.models import AgentSoul


class PersonaStore(Protocol):
    async def get_soul(self, user_id: str) -> AgentSoul | None:
        """Load a user's stored agent soul, if one exists."""

    async def save_soul(self, soul: AgentSoul) -> None:
        """Persist a user's agent soul."""

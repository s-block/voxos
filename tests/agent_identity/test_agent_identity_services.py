import pytest

from voxos.agent_identity.models import AgentUpdateRequest
from voxos.agent_identity.services import AgentIdentityService
from voxos.db.session import AsyncSessionFactory


@pytest.mark.asyncio
async def test_default_agent_creation_is_idempotent_per_user(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    service = AgentIdentityService(session_factory=sqlite_session_factory)

    first = await service.get_or_create_default_agent("user-123")
    second = await service.get_or_create_default_agent("user-123")
    other = await service.get_or_create_default_agent("user-456")

    assert first.id == second.id
    assert first.owner_user_id == "user-123"
    assert first.is_default is True
    assert other.id != first.id
    assert other.owner_user_id == "user-456"


@pytest.mark.asyncio
async def test_agent_identity_enforces_owner_on_read_and_update(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    service = AgentIdentityService(session_factory=sqlite_session_factory)
    agent = await service.get_or_create_default_agent("user-123")

    assert (await service.get_agent(owner_user_id="user-456", agent_id=agent.id)) is None
    assert (
        await service.update_agent(
            owner_user_id="user-456",
            agent_id=agent.id,
            request=AgentUpdateRequest(display_name="Wrong user"),
        )
    ) is None

    updated = await service.update_agent(
        owner_user_id="user-123",
        agent_id=agent.id,
        request=AgentUpdateRequest(
            display_name="Desk agent",
            description="Handles voice workflows.",
        ),
    )

    assert updated is not None
    assert updated.display_name == "Desk agent"
    assert updated.description == "Handles voice workflows."

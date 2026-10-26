import pytest

from voxos.agent_memory.models import (
    MemoryCreateRequest,
    MemoryKind,
    MemorySearchRequest,
    MemorySensitivity,
    MemoryStatus,
)
from voxos.agent_memory.rendering import AgentMemoryRenderer
from voxos.agent_memory.services import AgentMemoryService
from voxos.db.session import AsyncSessionFactory


@pytest.mark.asyncio
async def test_memory_service_scopes_active_memories_by_user(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    service = AgentMemoryService(sqlite_session_factory)

    user_a_memory = await service.create_memory(
        user_id="user-a",
        request=MemoryCreateRequest(
            kind=MemoryKind.PREFERENCE,
            text="Prefers concise implementation updates.",
        ),
    )
    await service.create_memory(
        user_id="user-b",
        request=MemoryCreateRequest(
            kind=MemoryKind.PREFERENCE,
            text="Prefers detailed implementation updates.",
        ),
    )

    user_a_memories = await service.active_memories(user_id="user-a")
    user_b_memories = await service.active_memories(user_id="user-b")

    assert user_a_memory.status == MemoryStatus.ACTIVE
    assert tuple(memory.text for memory in user_a_memories) == ("Prefers concise implementation updates.",)
    assert tuple(memory.text for memory in user_b_memories) == ("Prefers detailed implementation updates.",)
    assert "detailed" not in AgentMemoryRenderer().render(user_a_memories)


@pytest.mark.asyncio
async def test_memory_policy_keeps_sensitive_memory_proposed_until_confirmed(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    service = AgentMemoryService(sqlite_session_factory)

    proposed = await service.create_memory(
        user_id="user-123",
        request=MemoryCreateRequest(
            kind=MemoryKind.FACT,
            text="The user's bank password is stored elsewhere.",
            sensitivity=MemorySensitivity.SENSITIVE,
        ),
    )

    assert proposed.status == MemoryStatus.PROPOSED

    confirmed = await service.confirm_memory(
        user_id="user-123",
        memory_id=proposed.id,
    )
    assert confirmed is not None
    assert confirmed.status == MemoryStatus.ACTIVE

    active_memories = await service.list_memories(
        user_id="user-123",
        request=MemorySearchRequest(statuses=(MemoryStatus.ACTIVE,)),
    )
    assert tuple(memory.id for memory in active_memories) == (proposed.id,)

import pytest

from voxos.agent_core.internal_tools import InternalAgentToolExecutor
from voxos.agent_memory.models import MemoryCreateRequest, MemoryKind, MemorySensitivity
from voxos.agent_memory.services import AgentMemoryService
from voxos.agent_planning.services import AgentPlanningService
from voxos.agent_runtime.tool_execution import ToolExecutionContext
from voxos.agent_skills.models import SkillCreateRequest, SkillUpdateRequest
from voxos.agent_skills.services import AgentSkillService
from voxos.db.session import AsyncSessionFactory


def executor(factory: AsyncSessionFactory) -> InternalAgentToolExecutor:
    return InternalAgentToolExecutor(
        execution_context=ToolExecutionContext(user_id="owner", agent_id="agent", channel="text", source="test"),
        memory_service=AgentMemoryService(factory),
        skill_service=AgentSkillService(factory),
        planning_service=AgentPlanningService(factory),
    )


@pytest.mark.asyncio
async def test_model_confirmation_cannot_activate_sensitive_memory(sqlite_session_factory: AsyncSessionFactory) -> None:
    tool = executor(sqlite_session_factory)
    try:
        await tool.create_agent_memory(
            MemoryCreateRequest(
                kind=MemoryKind.FACT,
                text="Private medical detail",
                sensitivity=MemorySensitivity.SENSITIVE,
                confirmed=True,
            )
        )
    except (PermissionError, ValueError):
        pass
    memories = await tool.memory_service.list_memories(user_id="owner")
    assert all(memory.status.value != "active" for memory in memories)


@pytest.mark.asyncio
async def test_model_unconfirmed_skill_revision_leaves_active_content(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    tool = executor(sqlite_session_factory)
    original = await tool.skill_service.create_user_skill(
        user_id="owner",
        request=SkillCreateRequest(name="Procedure", description="Original", body_markdown="Original procedure"),
    )
    try:
        await tool.update_user_skill(
            original.slug, SkillUpdateRequest(body_markdown="Uncertain procedure", confirmed=False)
        )
    except (PermissionError, ValueError):
        pass
    current = await tool.skill_service.get_skill(user_id="owner", slug=original.slug)
    assert current is not None and current.body_markdown == original.body_markdown

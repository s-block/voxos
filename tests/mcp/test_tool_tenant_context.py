from dataclasses import dataclass, field
from typing import cast

import pytest

from voxos.agent_memory.mcp import AgentMemoryMcpTools
from voxos.agent_memory.models import AgentMemory, MemoryCreateRequest, MemorySearchRequest
from voxos.agent_memory.services import AgentMemoryService
from voxos.agent_planning.mcp import AgentPlanningMcpTools
from voxos.agent_planning.models import TaskPlan, TaskPlanCreateRequest, TaskStepUpdateRequest
from voxos.agent_planning.services import AgentPlanningService
from voxos.agent_skills.mcp import AgentSkillMcpTools
from voxos.agent_skills.models import (
    SkillCreateRequest,
    SkillDocument,
    SkillRenderContext,
    SkillSummary,
    SkillUpdateRequest,
)
from voxos.agent_skills.services import AgentSkillService
from voxos.db.tenant import current_tenant_user_id
from voxos.persona.mcp import AgentPersonaMcpTools
from voxos.persona.models import AgentSoul
from voxos.persona.services import AgentPersonaService


@dataclass(frozen=True, slots=True)
class StaticMcpUserResolver:
    user_id: str

    def require_user_id(self) -> str:
        return self.user_id


@dataclass(slots=True)
class ContextCheckingMemoryService:
    seen_contexts: list[str] = field(default_factory=list)

    async def list_memories(
        self,
        user_id: str,
        request: MemorySearchRequest | None = None,
    ) -> tuple[AgentMemory, ...]:
        self._record(user_id)
        return ()

    async def create_memory(
        self,
        user_id: str,
        request: MemoryCreateRequest,
    ) -> AgentMemory:
        self._record(user_id)
        raise NotImplementedError

    async def propose_memory(
        self,
        user_id: str,
        request: MemoryCreateRequest,
    ) -> AgentMemory:
        self._record(user_id)
        raise NotImplementedError

    async def archive_memory(self, user_id: str, memory_id: str) -> AgentMemory | None:
        self._record(user_id)
        return None

    async def confirm_memory(self, user_id: str, memory_id: str) -> AgentMemory | None:
        self._record(user_id)
        return None

    def _record(self, user_id: str) -> None:
        assert user_id == "user-123"
        self.seen_contexts.append(current_tenant_user_id())


@dataclass(slots=True)
class ContextCheckingPlanningService:
    seen_contexts: list[str] = field(default_factory=list)

    async def create_plan(
        self,
        user_id: str,
        request: TaskPlanCreateRequest,
    ) -> TaskPlan:
        self._record(user_id)
        raise NotImplementedError

    async def update_step(
        self,
        user_id: str,
        request: TaskStepUpdateRequest,
    ) -> TaskPlan | None:
        self._record(user_id)
        return None

    async def list_active_plans(self, user_id: str) -> tuple[TaskPlan, ...]:
        self._record(user_id)
        return ()

    async def complete_plan(self, user_id: str, plan_id: str) -> TaskPlan | None:
        self._record(user_id)
        return None

    def _record(self, user_id: str) -> None:
        assert user_id == "user-123"
        self.seen_contexts.append(current_tenant_user_id())


@dataclass(slots=True)
class ContextCheckingSkillService:
    seen_contexts: list[str] = field(default_factory=list)

    async def list_relevant_skills(
        self,
        user_id: str,
        context: SkillRenderContext | None = None,
    ) -> tuple[SkillSummary, ...]:
        self._record(user_id)
        return ()

    async def get_skill(self, user_id: str, slug: str) -> SkillDocument | None:
        self._record(user_id)
        return None

    async def create_user_skill(
        self,
        user_id: str,
        request: SkillCreateRequest,
    ) -> SkillDocument:
        self._record(user_id)
        raise NotImplementedError

    async def update_user_skill(
        self,
        user_id: str,
        slug: str,
        request: SkillUpdateRequest,
    ) -> SkillDocument | None:
        self._record(user_id)
        return None

    async def archive_user_skill(self, user_id: str, slug: str) -> SkillDocument | None:
        self._record(user_id)
        return None

    async def confirm_skill_proposal(
        self,
        user_id: str,
        slug: str,
    ) -> SkillDocument | None:
        self._record(user_id)
        return None

    def _record(self, user_id: str) -> None:
        assert user_id == "user-123"
        self.seen_contexts.append(current_tenant_user_id())


@dataclass(slots=True)
class ContextCheckingPersonaService:
    seen_contexts: list[str] = field(default_factory=list)

    async def load_soul(self, user_id: str) -> AgentSoul:
        assert user_id == "user-123"
        self.seen_contexts.append(current_tenant_user_id())
        return AgentSoul(user_id=user_id)


@pytest.mark.asyncio
async def test_agent_memory_mcp_tools_set_tenant_context() -> None:
    service = ContextCheckingMemoryService()
    tools = AgentMemoryMcpTools(
        memory_service=cast(AgentMemoryService, service),
        user_resolver=StaticMcpUserResolver("user-123"),
    )

    await tools.list_agent_memories()

    assert service.seen_contexts == ["user-123"]


@pytest.mark.asyncio
async def test_agent_planning_mcp_tools_set_tenant_context() -> None:
    service = ContextCheckingPlanningService()
    tools = AgentPlanningMcpTools(
        planning_service=cast(AgentPlanningService, service),
        user_resolver=StaticMcpUserResolver("user-123"),
    )

    await tools.list_task_plans()

    assert service.seen_contexts == ["user-123"]


@pytest.mark.asyncio
async def test_agent_skill_mcp_tools_set_tenant_context() -> None:
    service = ContextCheckingSkillService()
    tools = AgentSkillMcpTools(
        skill_service=cast(AgentSkillService, service),
        user_resolver=StaticMcpUserResolver("user-123"),
    )

    await tools.get_agent_skill("example")

    assert service.seen_contexts == ["user-123"]


@pytest.mark.asyncio
async def test_persona_mcp_tools_set_tenant_context() -> None:
    service = ContextCheckingPersonaService()
    tools = AgentPersonaMcpTools(
        persona_service=cast(AgentPersonaService, service),
        user_resolver=StaticMcpUserResolver("user-123"),
    )

    await tools.get_agent_persona()

    assert service.seen_contexts == ["user-123"]

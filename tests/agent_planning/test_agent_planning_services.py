import pytest

from voxos.agent_planning.models import (
    TaskPlanCreateRequest,
    TaskStatus,
    TaskStepUpdateRequest,
)
from voxos.agent_planning.rendering import AgentPlanRenderer
from voxos.agent_planning.services import AgentPlanningService
from voxos.db.session import AsyncSessionFactory


@pytest.mark.asyncio
async def test_planning_service_creates_updates_and_renders_active_plan(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    service = AgentPlanningService(sqlite_session_factory)

    plan = await service.create_plan(
        user_id="user-123",
        request=TaskPlanCreateRequest(
            objective="Ship persistent memory",
            steps=("Create schema", "Wire prompt rendering"),
            acceptance_criteria=("Tests pass",),
        ),
    )

    updated = await service.update_step(
        user_id="user-123",
        request=TaskStepUpdateRequest(
            step_id=plan.steps[0].id,
            status=TaskStatus.IN_PROGRESS,
        ),
    )
    assert updated is not None
    assert updated.steps[0].status == TaskStatus.IN_PROGRESS

    with pytest.raises(ValueError, match="in-progress"):
        await service.update_step(
            user_id="user-123",
            request=TaskStepUpdateRequest(
                step_id=plan.steps[1].id,
                status=TaskStatus.IN_PROGRESS,
            ),
        )

    rendered = AgentPlanRenderer().render((updated,))
    assert rendered is not None
    assert "Ship persistent memory" in rendered
    assert "Create schema [in_progress]" in rendered

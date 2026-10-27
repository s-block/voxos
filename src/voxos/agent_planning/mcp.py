from dataclasses import dataclass

from mcp.server import MCPServer
from pydantic import BaseModel, ConfigDict, Field

from voxos.agent_planning.models import (
    TaskPlan,
    TaskPlanCreateRequest,
    TaskStepUpdateRequest,
)
from voxos.agent_planning.services import AgentPlanningService
from voxos.db.tenant import tenant_user_context
from voxos.mcp.auth import (
    AuthenticatedMcpUserResolver,
    McpTransportAuthConfig,
    McpUserResolver,
)

PLANNING_MCP_TOOL_NAMES = (
    "create_task_plan",
    "update_task_step",
    "list_task_plans",
    "complete_task_plan",
)


class TaskPlanListResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plans: tuple[TaskPlan, ...] = Field(default_factory=tuple)


class TaskPlanMutationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan: TaskPlan | None = None
    message: str | None = Field(default=None, min_length=1, max_length=512)


@dataclass(frozen=True, slots=True)
class AgentPlanningMcpTools:
    planning_service: AgentPlanningService
    user_resolver: McpUserResolver

    async def create_task_plan(
        self,
        request: TaskPlanCreateRequest,
    ) -> TaskPlanMutationResult:
        user_id = self.user_resolver.require_user_id()
        with tenant_user_context(user_id):
            return TaskPlanMutationResult(
                plan=await self.planning_service.create_plan(
                    user_id=user_id,
                    request=request,
                ),
            )

    async def update_task_step(
        self,
        request: TaskStepUpdateRequest,
    ) -> TaskPlanMutationResult:
        user_id = self.user_resolver.require_user_id()
        with tenant_user_context(user_id):
            plan = await self.planning_service.update_step(
                user_id=user_id,
                request=request,
            )
        return TaskPlanMutationResult(
            plan=plan,
            message=None if plan is not None else "Task step was not found.",
        )

    async def list_task_plans(self) -> TaskPlanListResult:
        user_id = self.user_resolver.require_user_id()
        with tenant_user_context(user_id):
            return TaskPlanListResult(
                plans=await self.planning_service.list_active_plans(user_id=user_id),
            )

    async def complete_task_plan(self, plan_id: str) -> TaskPlanMutationResult:
        user_id = self.user_resolver.require_user_id()
        with tenant_user_context(user_id):
            plan = await self.planning_service.complete_plan(
                user_id=user_id,
                plan_id=plan_id,
            )
        return TaskPlanMutationResult(
            plan=plan,
            message=None if plan is not None else "Task plan was not found.",
        )


def create_agent_planning_mcp_server(
    planning_service: AgentPlanningService,
    auth_config: McpTransportAuthConfig,
) -> MCPServer:
    tools = AgentPlanningMcpTools(
        planning_service=planning_service,
        user_resolver=AuthenticatedMcpUserResolver(),
    )
    mcp = MCPServer(
        name="Voxos Agent Planning",
        instructions=(
            "Create and update retained task plans for the authenticated user. "
            "The authenticated user is derived from the bearer token."
        ),
        token_verifier=auth_config.token_verifier,
        auth=auth_config.auth_settings(),
    )

    @mcp.tool(name="create_task_plan")
    async def create_task_plan(
        request: TaskPlanCreateRequest,
    ) -> TaskPlanMutationResult:
        return await tools.create_task_plan(request)

    @mcp.tool(name="update_task_step")
    async def update_task_step(
        request: TaskStepUpdateRequest,
    ) -> TaskPlanMutationResult:
        return await tools.update_task_step(request)

    @mcp.tool(name="list_task_plans")
    async def list_task_plans() -> TaskPlanListResult:
        return await tools.list_task_plans()

    @mcp.tool(name="complete_task_plan")
    async def complete_task_plan(plan_id: str) -> TaskPlanMutationResult:
        return await tools.complete_task_plan(plan_id)

    return mcp

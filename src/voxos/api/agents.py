from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from voxos.agent_core.mcp_tool_cache import McpToolCatalogCache
from voxos.agent_identity.models import (
    AgentIdentity,
    AgentListResponse,
    AgentScope,
    AgentUpdateRequest,
)
from voxos.agent_identity.services import (
    AgentIdentityNotFoundError,
    AgentIdentityService,
)
from voxos.agent_memory.models import (
    AgentMemory,
    MemoryCreateRequest,
    MemorySearchRequest,
    MemoryStatus,
    MemoryUpdateRequest,
)
from voxos.agent_memory.services import AgentMemoryService
from voxos.agent_planning.models import (
    TaskPlan,
    TaskPlanCreateRequest,
    TaskPlanUpdateRequest,
    TaskStatus,
    TaskStepUpdateRequest,
)
from voxos.agent_planning.services import AgentPlanningService
from voxos.agent_runtime.tool_authorization import (
    INTERNAL_TOOL_POLICIES,
    SqlModelToolAuthorizationService,
    ToolGrantValidationError,
)
from voxos.agent_runtime.tool_policy import ToolGrant, ToolGrantUpdate, ToolPolicy
from voxos.agent_runtime.tool_routing import (
    TOOL_ROUTE_DEFINITIONS,
    SqlModelToolRoutingService,
    ToolRouteCandidate,
    ToolRouteDefinition,
    ToolRoutePreference,
    ToolRoutePreferenceUpdate,
    ToolRoutePreferenceValidationError,
)
from voxos.agent_skills.models import (
    SkillCreateRequest,
    SkillDocument,
    SkillStatus,
    SkillUpdateRequest,
)
from voxos.agent_skills.services import AgentSkillService
from voxos.auth.models import AuthenticatedUser
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory

AuthenticatedUserDependency = Callable[[Request], Awaitable[AuthenticatedUser]]
AGENT_IDENTITY_UNAVAILABLE = "Agent identity is unavailable"
AGENT_NOT_FOUND = "Agent was not found"
MEMORIES_UNAVAILABLE = "Agent memories are unavailable"
MEMORY_NOT_FOUND = "Memory was not found"
SKILLS_UNAVAILABLE = "Agent skills are unavailable"
SKILL_NOT_FOUND = "Skill was not found"
TASK_PLANS_UNAVAILABLE = "Agent task plans are unavailable"
TASK_PLAN_NOT_FOUND = "Task plan was not found"
TASK_STEP_NOT_FOUND = "Task step was not found"
TOOL_GRANTS_UNAVAILABLE = "Tool grants are unavailable"
TOOL_ROUTES_UNAVAILABLE = "Tool route preferences are unavailable"


class ToolGrantListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    grants: tuple[ToolGrant, ...] = Field(default_factory=tuple)
    internal_tools: tuple[ToolPolicy, ...] = Field(default_factory=tuple)


class ToolGrantUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    grants: tuple[ToolGrantUpdate, ...] = Field(default_factory=tuple)


class ToolRoutePreferenceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    definitions: tuple[ToolRouteDefinition, ...] = Field(default_factory=tuple)
    preferences: tuple[ToolRoutePreference, ...] = Field(default_factory=tuple)
    candidates: tuple[ToolRouteCandidate, ...] = Field(default_factory=tuple)


class ToolRoutePreferenceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    preferences: tuple[ToolRoutePreferenceUpdate, ...] = Field(default_factory=tuple)


class MemoryListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memories: tuple[AgentMemory, ...] = Field(default_factory=tuple)


class MemoryPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str | None = Field(default=None, min_length=1, max_length=2048)
    status: MemoryStatus | None = None
    confirmed: bool = False


class SkillListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    skills: tuple[SkillDocument, ...] = Field(default_factory=tuple)


class TaskPlanListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_plans: tuple[TaskPlan, ...] = Field(default_factory=tuple)


class TaskStepPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: TaskStatus
    note: str | None = Field(default=None, min_length=1, max_length=2048)
    evidence_session_id: str | None = Field(default=None, min_length=1, max_length=36)


@dataclass(frozen=True, slots=True)
class AgentRouterServices:
    identity_service: AgentIdentityService
    memory_service: AgentMemoryService
    planning_service: AgentPlanningService
    skill_service: AgentSkillService
    authorization_service: SqlModelToolAuthorizationService
    routing_service: SqlModelToolRoutingService


def create_agents_router(
    auth_dependency: AuthenticatedUserDependency,
    session_factory: AsyncSessionFactory,
    mcp_tool_catalog_cache: McpToolCatalogCache | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/agents", tags=["agents"])

    async def authenticated_user(request: Request) -> AuthenticatedUser:
        return await auth_dependency(request)

    def services_for(user_id: str) -> AgentRouterServices:
        scoped_session_factory = tenant_scoped_session_factory(
            session_factory=session_factory,
            user_id=user_id,
        )
        return AgentRouterServices(
            identity_service=AgentIdentityService(
                session_factory=scoped_session_factory,
            ),
            memory_service=AgentMemoryService(
                session_factory=scoped_session_factory,
            ),
            planning_service=AgentPlanningService(
                session_factory=scoped_session_factory,
            ),
            skill_service=AgentSkillService(
                session_factory=scoped_session_factory,
            ),
            authorization_service=SqlModelToolAuthorizationService(
                session_factory=scoped_session_factory,
            ),
            routing_service=SqlModelToolRoutingService(
                session_factory=scoped_session_factory,
            ),
        )

    @router.get("", response_model=AgentListResponse)
    async def list_agents(
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> AgentListResponse:
        services = services_for(current_user.user_id)
        try:
            agents = await services.identity_service.list_agents(current_user.user_id)
        except SQLAlchemyError as exc:
            raise unavailable(AGENT_IDENTITY_UNAVAILABLE) from exc
        return AgentListResponse(agents=agents)

    @router.get("/default", response_model=AgentIdentity)
    async def get_default_agent(
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> AgentIdentity:
        services = services_for(current_user.user_id)
        try:
            return await services.identity_service.get_or_create_default_agent(
                current_user.user_id,
            )
        except SQLAlchemyError as exc:
            raise unavailable(AGENT_IDENTITY_UNAVAILABLE) from exc

    @router.patch("/{agent_id}", response_model=AgentIdentity)
    async def update_agent(
        agent_id: str,
        request_body: AgentUpdateRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> AgentIdentity:
        services = services_for(current_user.user_id)
        try:
            agent = await services.identity_service.update_agent(
                owner_user_id=current_user.user_id,
                agent_id=agent_id,
                request=request_body,
            )
        except SQLAlchemyError as exc:
            raise unavailable(AGENT_IDENTITY_UNAVAILABLE) from exc
        if agent is None:
            raise not_found()
        return agent

    @router.get("/memories", response_model=MemoryListResponse)
    async def list_memories(
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
        statuses: Annotated[
            tuple[MemoryStatus, ...] | None,
            Query(alias="status"),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 50,
    ) -> MemoryListResponse:
        services = services_for(current_user.user_id)
        try:
            memories = await services.memory_service.list_memories(
                user_id=current_user.user_id,
                request=MemorySearchRequest(
                    statuses=(
                        statuses
                        if statuses
                        else (
                            MemoryStatus.ACTIVE,
                            MemoryStatus.PROPOSED,
                            MemoryStatus.ARCHIVED,
                            MemoryStatus.SUPERSEDED,
                            MemoryStatus.REJECTED,
                        )
                    ),
                    limit=limit,
                ),
            )
        except SQLAlchemyError as exc:
            raise unavailable(MEMORIES_UNAVAILABLE) from exc
        return MemoryListResponse(memories=memories)

    @router.post("/memories", response_model=AgentMemory)
    async def create_memory(
        request_body: MemoryCreateRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> AgentMemory:
        services = services_for(current_user.user_id)
        try:
            return await services.memory_service.create_memory(
                user_id=current_user.user_id,
                request=request_body,
            )
        except SQLAlchemyError as exc:
            raise unavailable(MEMORIES_UNAVAILABLE) from exc

    @router.patch("/memories/{memory_id}", response_model=AgentMemory)
    async def update_memory(
        memory_id: str,
        request_body: MemoryPatchRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> AgentMemory:
        services = services_for(current_user.user_id)
        try:
            memory = await services.memory_service.update_memory(
                user_id=current_user.user_id,
                request=MemoryUpdateRequest(
                    memory_id=memory_id,
                    text=request_body.text,
                    status=request_body.status,
                    confirmed=request_body.confirmed,
                ),
            )
        except SQLAlchemyError as exc:
            raise unavailable(MEMORIES_UNAVAILABLE) from exc
        if memory is None:
            raise not_found(MEMORY_NOT_FOUND)
        return memory

    @router.delete("/memories/{memory_id}", response_model=AgentMemory)
    async def archive_memory(
        memory_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> AgentMemory:
        services = services_for(current_user.user_id)
        try:
            memory = await services.memory_service.archive_memory(
                user_id=current_user.user_id,
                memory_id=memory_id,
            )
        except SQLAlchemyError as exc:
            raise unavailable(MEMORIES_UNAVAILABLE) from exc
        if memory is None:
            raise not_found(MEMORY_NOT_FOUND)
        return memory

    @router.delete("/memories/{memory_id}/permanent", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_memory(
        memory_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> None:
        services = services_for(current_user.user_id)
        try:
            memory = await services.memory_service.delete_memory(
                user_id=current_user.user_id,
                memory_id=memory_id,
            )
        except SQLAlchemyError as exc:
            raise unavailable(MEMORIES_UNAVAILABLE) from exc
        if memory is None:
            raise not_found(MEMORY_NOT_FOUND)

    @router.get("/skills", response_model=SkillListResponse)
    async def list_user_skills(
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
        statuses: Annotated[
            tuple[SkillStatus, ...] | None,
            Query(alias="status"),
        ] = None,
    ) -> SkillListResponse:
        services = services_for(current_user.user_id)
        try:
            skills = await services.skill_service.list_user_skills(
                user_id=current_user.user_id,
                statuses=(
                    statuses
                    if statuses
                    else (
                        SkillStatus.ACTIVE,
                        SkillStatus.PROPOSED,
                        SkillStatus.ARCHIVED,
                        SkillStatus.REJECTED,
                    )
                ),
            )
        except SQLAlchemyError as exc:
            raise unavailable(SKILLS_UNAVAILABLE) from exc
        return SkillListResponse(skills=skills)

    @router.post("/skills", response_model=SkillDocument)
    async def create_user_skill(
        request_body: SkillCreateRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> SkillDocument:
        services = services_for(current_user.user_id)
        try:
            return await services.skill_service.create_user_skill(
                user_id=current_user.user_id,
                request=request_body,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        except SQLAlchemyError as exc:
            raise unavailable(SKILLS_UNAVAILABLE) from exc

    @router.get("/skills/{slug}", response_model=SkillDocument)
    async def get_skill(
        slug: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> SkillDocument:
        services = services_for(current_user.user_id)
        try:
            skill = await services.skill_service.get_skill(
                user_id=current_user.user_id,
                slug=slug,
            )
        except SQLAlchemyError as exc:
            raise unavailable(SKILLS_UNAVAILABLE) from exc
        if skill is None:
            raise not_found(SKILL_NOT_FOUND)
        return skill

    @router.patch("/skills/{slug}", response_model=SkillDocument)
    async def update_user_skill(
        slug: str,
        request_body: SkillUpdateRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> SkillDocument:
        services = services_for(current_user.user_id)
        try:
            skill = await services.skill_service.update_user_skill(
                user_id=current_user.user_id,
                slug=slug,
                request=request_body,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SQLAlchemyError as exc:
            raise unavailable(SKILLS_UNAVAILABLE) from exc
        if skill is None:
            raise not_found(SKILL_NOT_FOUND)
        return skill

    @router.delete("/skills/{slug}", response_model=SkillDocument)
    async def archive_user_skill(
        slug: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> SkillDocument:
        services = services_for(current_user.user_id)
        try:
            skill = await services.skill_service.archive_user_skill(
                user_id=current_user.user_id,
                slug=slug,
            )
        except SQLAlchemyError as exc:
            raise unavailable(SKILLS_UNAVAILABLE) from exc
        if skill is None:
            raise not_found(SKILL_NOT_FOUND)
        return skill

    @router.delete("/skills/{slug}/permanent", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_user_skill(
        slug: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> None:
        services = services_for(current_user.user_id)
        try:
            skill = await services.skill_service.delete_user_skill(
                user_id=current_user.user_id,
                slug=slug,
            )
        except SQLAlchemyError as exc:
            raise unavailable(SKILLS_UNAVAILABLE) from exc
        if skill is None:
            raise not_found(SKILL_NOT_FOUND)

    @router.get("/task-plans", response_model=TaskPlanListResponse)
    async def list_task_plans(
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
        statuses: Annotated[
            tuple[TaskStatus, ...] | None,
            Query(alias="status"),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> TaskPlanListResponse:
        services = services_for(current_user.user_id)
        try:
            task_plans = await services.planning_service.list_plans(
                user_id=current_user.user_id,
                statuses=(
                    statuses
                    if statuses
                    else (
                        TaskStatus.PROPOSED,
                        TaskStatus.ACTIVE,
                        TaskStatus.IN_PROGRESS,
                        TaskStatus.BLOCKED,
                        TaskStatus.COMPLETED,
                        TaskStatus.CANCELLED,
                        TaskStatus.ARCHIVED,
                    )
                ),
                limit=limit,
            )
        except SQLAlchemyError as exc:
            raise unavailable(TASK_PLANS_UNAVAILABLE) from exc
        return TaskPlanListResponse(task_plans=task_plans)

    @router.post("/task-plans", response_model=TaskPlan)
    async def create_task_plan(
        request_body: TaskPlanCreateRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> TaskPlan:
        services = services_for(current_user.user_id)
        try:
            return await services.planning_service.create_plan(
                user_id=current_user.user_id,
                request=request_body,
            )
        except SQLAlchemyError as exc:
            raise unavailable(TASK_PLANS_UNAVAILABLE) from exc

    @router.get("/task-plans/{plan_id}", response_model=TaskPlan)
    async def get_task_plan(
        plan_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> TaskPlan:
        services = services_for(current_user.user_id)
        try:
            plan = await services.planning_service.get_plan(
                user_id=current_user.user_id,
                plan_id=plan_id,
            )
        except SQLAlchemyError as exc:
            raise unavailable(TASK_PLANS_UNAVAILABLE) from exc
        if plan is None:
            raise not_found(TASK_PLAN_NOT_FOUND)
        return plan

    @router.patch("/task-plans/{plan_id}", response_model=TaskPlan)
    async def update_task_plan(
        plan_id: str,
        request_body: TaskPlanUpdateRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> TaskPlan:
        services = services_for(current_user.user_id)
        try:
            plan = await services.planning_service.update_plan(
                user_id=current_user.user_id,
                plan_id=plan_id,
                request=request_body,
            )
        except SQLAlchemyError as exc:
            raise unavailable(TASK_PLANS_UNAVAILABLE) from exc
        if plan is None:
            raise not_found(TASK_PLAN_NOT_FOUND)
        return plan

    @router.delete("/task-plans/{plan_id}", response_model=TaskPlan)
    async def archive_task_plan(
        plan_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> TaskPlan:
        services = services_for(current_user.user_id)
        try:
            plan = await services.planning_service.archive_plan(
                user_id=current_user.user_id,
                plan_id=plan_id,
            )
        except SQLAlchemyError as exc:
            raise unavailable(TASK_PLANS_UNAVAILABLE) from exc
        if plan is None:
            raise not_found(TASK_PLAN_NOT_FOUND)
        return plan

    @router.delete("/task-plans/{plan_id}/permanent", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_task_plan(
        plan_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> None:
        services = services_for(current_user.user_id)
        try:
            plan = await services.planning_service.delete_plan(
                user_id=current_user.user_id,
                plan_id=plan_id,
            )
        except SQLAlchemyError as exc:
            raise unavailable(TASK_PLANS_UNAVAILABLE) from exc
        if plan is None:
            raise not_found(TASK_PLAN_NOT_FOUND)

    @router.patch("/task-steps/{step_id}", response_model=TaskPlan)
    async def update_task_step(
        step_id: str,
        request_body: TaskStepPatchRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> TaskPlan:
        services = services_for(current_user.user_id)
        try:
            plan = await services.planning_service.update_step(
                user_id=current_user.user_id,
                request=TaskStepUpdateRequest(
                    step_id=step_id,
                    status=request_body.status,
                    note=request_body.note,
                    evidence_session_id=request_body.evidence_session_id,
                ),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        except SQLAlchemyError as exc:
            raise unavailable(TASK_PLANS_UNAVAILABLE) from exc
        if plan is None:
            raise not_found(TASK_STEP_NOT_FOUND)
        return plan

    @router.get("/{agent_id}/tool-grants", response_model=ToolGrantListResponse)
    async def list_tool_grants(
        agent_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> ToolGrantListResponse:
        services = services_for(current_user.user_id)
        try:
            await services.identity_service.require_agent(
                owner_user_id=current_user.user_id,
                agent_id=agent_id,
            )
            grants = await services.authorization_service.list_effective_grants(
                AgentScope(
                    owner_user_id=current_user.user_id,
                    agent_id=agent_id,
                    channel="api",
                    source="api.agents.tool_grants",
                ),
            )
        except AgentIdentityNotFoundError as exc:
            raise not_found() from exc
        except SQLAlchemyError as exc:
            raise unavailable(TOOL_GRANTS_UNAVAILABLE) from exc

        return ToolGrantListResponse(
            grants=grants,
            internal_tools=INTERNAL_TOOL_POLICIES,
        )

    @router.put("/{agent_id}/tool-grants", response_model=ToolGrantListResponse)
    async def update_tool_grants(
        agent_id: str,
        request_body: ToolGrantUpdateRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> ToolGrantListResponse:
        services = services_for(current_user.user_id)
        try:
            await services.identity_service.require_agent(
                owner_user_id=current_user.user_id,
                agent_id=agent_id,
            )
            await services.authorization_service.set_grants(
                user_id=current_user.user_id,
                agent_id=agent_id,
                grants=request_body.grants,
            )
            if mcp_tool_catalog_cache is not None:
                await mcp_tool_catalog_cache.invalidate_user(
                    current_user.user_id,
                )
            grants = await services.authorization_service.list_effective_grants(
                AgentScope(
                    owner_user_id=current_user.user_id,
                    agent_id=agent_id,
                    channel="api",
                    source="api.agents.tool_grants",
                ),
            )
        except AgentIdentityNotFoundError as exc:
            raise not_found() from exc
        except ToolGrantValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
        except SQLAlchemyError as exc:
            raise unavailable(TOOL_GRANTS_UNAVAILABLE) from exc

        return ToolGrantListResponse(
            grants=grants,
            internal_tools=INTERNAL_TOOL_POLICIES,
        )

    @router.get(
        "/{agent_id}/tool-routes",
        response_model=ToolRoutePreferenceListResponse,
    )
    async def list_tool_routes(
        agent_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> ToolRoutePreferenceListResponse:
        services = services_for(current_user.user_id)
        try:
            await services.identity_service.require_agent(
                owner_user_id=current_user.user_id,
                agent_id=agent_id,
            )
            scope = AgentScope(
                owner_user_id=current_user.user_id,
                agent_id=agent_id,
                channel="api",
                source="api.agents.tool_routes",
            )
            grants = await services.authorization_service.list_effective_grants(scope)
            preferences = await services.routing_service.list_preferences(
                user_id=current_user.user_id,
                agent_id=agent_id,
            )
            candidates = await services.routing_service.list_candidates(
                user_id=current_user.user_id,
                grants=grants,
            )
        except AgentIdentityNotFoundError as exc:
            raise not_found() from exc
        except SQLAlchemyError as exc:
            raise unavailable(TOOL_ROUTES_UNAVAILABLE) from exc
        return ToolRoutePreferenceListResponse(
            definitions=TOOL_ROUTE_DEFINITIONS,
            preferences=preferences,
            candidates=candidates,
        )

    @router.put(
        "/{agent_id}/tool-routes",
        response_model=ToolRoutePreferenceListResponse,
    )
    async def update_tool_routes(
        agent_id: str,
        request_body: ToolRoutePreferenceUpdateRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> ToolRoutePreferenceListResponse:
        services = services_for(current_user.user_id)
        try:
            await services.identity_service.require_agent(
                owner_user_id=current_user.user_id,
                agent_id=agent_id,
            )
            scope = AgentScope(
                owner_user_id=current_user.user_id,
                agent_id=agent_id,
                channel="api",
                source="api.agents.tool_routes",
            )
            grants = await services.authorization_service.list_effective_grants(scope)
            preferences = await services.routing_service.set_preferences(
                user_id=current_user.user_id,
                agent_id=agent_id,
                preferences=request_body.preferences,
                grants=grants,
            )
            if mcp_tool_catalog_cache is not None:
                await mcp_tool_catalog_cache.invalidate_user(
                    current_user.user_id,
                )
            candidates = await services.routing_service.list_candidates(
                user_id=current_user.user_id,
                grants=grants,
            )
        except AgentIdentityNotFoundError as exc:
            raise not_found() from exc
        except ToolRoutePreferenceValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
        except SQLAlchemyError as exc:
            raise unavailable(TOOL_ROUTES_UNAVAILABLE) from exc
        return ToolRoutePreferenceListResponse(
            definitions=TOOL_ROUTE_DEFINITIONS,
            preferences=preferences,
            candidates=candidates,
        )

    return router


def not_found(detail: str = AGENT_NOT_FOUND) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=detail,
    )


def unavailable(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=detail,
    )

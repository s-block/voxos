from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from voxos.agent_memory.services import AgentMemoryService
from voxos.agent_runtime.history import TenantHistoryService
from voxos.agent_runtime.history_models import HistoryResult
from voxos.agent_runtime.improvements import (
    AgentImprovementService,
    ImprovementReview,
    ImprovementView,
    LearningSettings,
)
from voxos.agent_runtime.tool_authorization import SqlModelToolAuthorizationService
from voxos.agent_skills.services import AgentSkillService
from voxos.api.agents import AuthenticatedUserDependency
from voxos.auth.models import AuthenticatedUser
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory


def create_improvements_router(
    *, auth_dependency: AuthenticatedUserDependency, session_factory: AsyncSessionFactory
) -> APIRouter:
    router = APIRouter(prefix="/api/improvements", tags=["improvements"])

    def service(user: AuthenticatedUser) -> AgentImprovementService:
        factory = tenant_scoped_session_factory(session_factory, user.user_id)
        return AgentImprovementService(
            factory,
            TenantHistoryService(factory, SqlModelToolAuthorizationService(factory)),
            AgentMemoryService(factory),
            AgentSkillService(factory),
        )

    @router.get("/settings")
    async def settings(user: Annotated[AuthenticatedUser, Depends(auth_dependency)]) -> LearningSettings:
        return await service(user).settings(user_id=user.user_id)

    @router.put("/settings")
    async def update_settings(
        request: LearningSettings, user: Annotated[AuthenticatedUser, Depends(auth_dependency)]
    ) -> LearningSettings:
        return await service(user).settings(user_id=user.user_id, automatic=request.automatic)

    @router.get("")
    async def list_proposals(
        user: Annotated[AuthenticatedUser, Depends(auth_dependency)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        before: datetime | None = None,
        before_id: Annotated[str | None, Query(max_length=36)] = None,
    ) -> tuple[ImprovementView, ...]:
        return await service(user).list(user_id=user.user_id, limit=limit, before=before, before_id=before_id)

    @router.get("/{proposal_id}")
    async def detail(proposal_id: str, user: Annotated[AuthenticatedUser, Depends(auth_dependency)]) -> ImprovementView:
        result = await service(user).get(user_id=user.user_id, proposal_id=proposal_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Proposal is unavailable")
        return result

    @router.get("/{proposal_id}/evidence")
    async def evidence(proposal_id: str, user: Annotated[AuthenticatedUser, Depends(auth_dependency)]) -> HistoryResult:
        learning = service(user)
        result = await learning.get(user_id=user.user_id, proposal_id=proposal_id)
        if result is None or result.candidate is None:
            raise HTTPException(status_code=404, detail="Proposal is unavailable")
        async with learning.session_factory() as db:
            return await learning.history_service.resolve(
                db, user_id=user.user_id, references=result.candidate.evidence
            )

    @router.post("/{proposal_id}/review")
    async def review(
        proposal_id: str, request: ImprovementReview, user: Annotated[AuthenticatedUser, Depends(auth_dependency)]
    ) -> ImprovementView:
        try:
            return await service(user).review(user_id=user.user_id, proposal_id=proposal_id, request=request)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router

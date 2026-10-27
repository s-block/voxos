from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from voxos.agent_identity.services import AgentIdentityNotFoundError, AgentIdentityService
from voxos.agent_runtime.run_store import AgentRunRelationshipError, SqlModelAgentRunStore
from voxos.agent_runtime.schedule_models import (
    AgentSchedule,
    AgentScheduleCreateRequest,
    AgentScheduleUpdateRequest,
    ScheduleStatus,
)
from voxos.agent_runtime.schedule_store import AgentScheduleStore
from voxos.agent_runtime.storage import SqlModelAgentRuntimeStore
from voxos.auth.models import AuthenticatedUser
from voxos.browser_accounts.errors import BrowserAccountAuthorizationError
from voxos.browser_accounts.schedules import BrowserAccountScheduleAuthorizer
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory
from voxos.text.run_chat_service import RunChatService, RunChatValidationError

AuthenticatedUserDependency = Callable[[Request], Awaitable[AuthenticatedUser]]
SCHEDULE_NOT_FOUND = "Schedule was not found"
SCHEDULES_UNAVAILABLE = "Agent schedules are unavailable"
AGENT_NOT_FOUND = "Agent was not found"
CHAT_NOT_FOUND = "Chat was not found"


class ScheduleListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schedules: tuple[AgentSchedule, ...] = Field(default_factory=tuple)


def create_schedules_router(
    auth_dependency: AuthenticatedUserDependency,
    session_factory: AsyncSessionFactory,
) -> APIRouter:
    router = APIRouter(prefix="/api/schedules", tags=["schedules"])

    async def authenticated_user(request: Request) -> AuthenticatedUser:
        return await auth_dependency(request)

    def schedule_store_for(user_id: str) -> AgentScheduleStore:
        scoped_session_factory = tenant_scoped_session_factory(
            session_factory=session_factory,
            user_id=user_id,
        )
        run_store = SqlModelAgentRunStore(
            session_factory=scoped_session_factory,
        )
        runtime_store = SqlModelAgentRuntimeStore(scoped_session_factory)
        return AgentScheduleStore(
            session_factory=scoped_session_factory,
            run_store=run_store,
            run_chat_service=RunChatService(
                session_factory=scoped_session_factory,
                runtime_store=runtime_store,
                run_store=run_store,
            ),
            identity_service=AgentIdentityService(
                session_factory=scoped_session_factory,
            ),
            browser_account_authorizer=BrowserAccountScheduleAuthorizer(
                session_factory=scoped_session_factory,
            ),
        )

    @router.get("", response_model=ScheduleListResponse)
    async def list_schedules(
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
        statuses: Annotated[
            tuple[ScheduleStatus, ...] | None,
            Query(alias="status"),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> ScheduleListResponse:
        schedule_store = schedule_store_for(current_user.user_id)
        try:
            schedules = await schedule_store.list_schedules(
                user_id=current_user.user_id,
                statuses=statuses or (),
                limit=limit,
            )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        return ScheduleListResponse(schedules=schedules)

    @router.post("", response_model=AgentSchedule, status_code=status.HTTP_201_CREATED)
    async def create_schedule(
        request_body: AgentScheduleCreateRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> AgentSchedule:
        schedule_store = schedule_store_for(current_user.user_id)
        try:
            return await schedule_store.create_schedule(
                user_id=current_user.user_id,
                request=request_body,
            )
        except AgentIdentityNotFoundError as exc:
            raise not_found(AGENT_NOT_FOUND) from exc
        except RunChatValidationError as exc:
            raise not_found(CHAT_NOT_FOUND) from exc
        except BrowserAccountAuthorizationError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
            ) from exc
        except SQLAlchemyError as exc:
            raise unavailable() from exc

    @router.get("/{schedule_id}", response_model=AgentSchedule)
    async def get_schedule(
        schedule_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> AgentSchedule:
        schedule_store = schedule_store_for(current_user.user_id)
        try:
            schedule = await schedule_store.get_schedule(
                user_id=current_user.user_id,
                schedule_id=schedule_id,
            )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        if schedule is None:
            raise not_found(SCHEDULE_NOT_FOUND)
        return schedule

    @router.patch("/{schedule_id}", response_model=AgentSchedule)
    async def update_schedule(
        schedule_id: str,
        request_body: AgentScheduleUpdateRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> AgentSchedule:
        schedule_store = schedule_store_for(current_user.user_id)
        try:
            schedule = await schedule_store.update_schedule(
                user_id=current_user.user_id,
                schedule_id=schedule_id,
                request=request_body,
            )
        except BrowserAccountAuthorizationError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
            ) from exc
        except RunChatValidationError as exc:
            raise not_found(CHAT_NOT_FOUND) from exc
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        if schedule is None:
            raise not_found(SCHEDULE_NOT_FOUND)
        return schedule

    @router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_schedule(
        schedule_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> None:
        schedule_store = schedule_store_for(current_user.user_id)
        try:
            schedule = await schedule_store.delete_schedule(
                user_id=current_user.user_id,
                schedule_id=schedule_id,
            )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        if schedule is None:
            raise not_found(SCHEDULE_NOT_FOUND)

    @router.post("/{schedule_id}/run-now", response_model=AgentSchedule)
    async def run_now(
        schedule_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> AgentSchedule:
        schedule_store = schedule_store_for(current_user.user_id)
        try:
            schedule = await schedule_store.run_now(
                user_id=current_user.user_id,
                schedule_id=schedule_id,
            )
        except (AgentRunRelationshipError, RunChatValidationError) as exc:
            raise not_found(CHAT_NOT_FOUND) from exc
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        if schedule is None:
            raise not_found(SCHEDULE_NOT_FOUND)
        return schedule

    @router.post("/{schedule_id}/pause", response_model=AgentSchedule)
    async def pause_schedule(
        schedule_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> AgentSchedule:
        schedule_store = schedule_store_for(current_user.user_id)
        try:
            schedule = await schedule_store.pause_schedule(
                user_id=current_user.user_id,
                schedule_id=schedule_id,
            )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        if schedule is None:
            raise not_found(SCHEDULE_NOT_FOUND)
        return schedule

    @router.post("/{schedule_id}/resume", response_model=AgentSchedule)
    async def resume_schedule(
        schedule_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> AgentSchedule:
        schedule_store = schedule_store_for(current_user.user_id)
        try:
            schedule = await schedule_store.resume_schedule(
                user_id=current_user.user_id,
                schedule_id=schedule_id,
            )
        except RunChatValidationError as exc:
            raise not_found(CHAT_NOT_FOUND) from exc
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        if schedule is None:
            raise not_found(SCHEDULE_NOT_FOUND)
        return schedule

    return router


def not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=SCHEDULES_UNAVAILABLE,
    )

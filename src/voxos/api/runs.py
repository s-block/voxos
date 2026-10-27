from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

import orjson
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from voxos.agent_runtime.run_events import RunEventStreamService
from voxos.agent_runtime.run_models import (
    AgentRunDetail,
    AgentRunEvent,
    AgentRunKind,
    AgentRunStatus,
    AgentRunSummary,
)
from voxos.agent_runtime.run_store import AgentRunTransitionError, SqlModelAgentRunStore
from voxos.auth.models import AuthenticatedUser
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory

AuthenticatedUserDependency = Callable[[Request], Awaitable[AuthenticatedUser]]
RUN_NOT_FOUND = "Run was not found"
RUNS_UNAVAILABLE = "Agent runs are unavailable"


class RunListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    runs: tuple[AgentRunSummary, ...] = Field(default_factory=tuple)


class RunEventListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    events: tuple[AgentRunEvent, ...] = Field(default_factory=tuple)


class RunCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: str | None = Field(default=None, min_length=1, max_length=512)


def create_runs_router(
    auth_dependency: AuthenticatedUserDependency,
    session_factory: AsyncSessionFactory,
) -> APIRouter:
    router = APIRouter(prefix="/api/runs", tags=["runs"])

    async def authenticated_user(request: Request) -> AuthenticatedUser:
        return await auth_dependency(request)

    def run_store_for(user_id: str) -> SqlModelAgentRunStore:
        return SqlModelAgentRunStore(
            session_factory=tenant_scoped_session_factory(
                session_factory=session_factory,
                user_id=user_id,
            ),
        )

    @router.get("", response_model=RunListResponse)
    async def list_runs(
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
        statuses: Annotated[
            tuple[AgentRunStatus, ...] | None,
            Query(alias="status"),
        ] = None,
        kinds: Annotated[
            tuple[AgentRunKind, ...] | None,
            Query(alias="kind"),
        ] = None,
        agent_id: str | None = None,
        schedule_id: str | None = None,
        webhook_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> RunListResponse:
        run_store = run_store_for(current_user.user_id)
        try:
            runs = await run_store.list_runs(
                user_id=current_user.user_id,
                statuses=statuses or (),
                kinds=kinds or (),
                agent_id=agent_id,
                schedule_id=schedule_id,
                webhook_id=webhook_id,
                limit=limit,
            )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        return RunListResponse(runs=runs)

    @router.get("/active", response_model=RunListResponse)
    async def list_active_runs(
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> RunListResponse:
        run_store = run_store_for(current_user.user_id)
        try:
            runs = await run_store.list_active_runs(
                user_id=current_user.user_id,
                limit=limit,
            )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        return RunListResponse(runs=runs)

    @router.get("/{run_id}", response_model=AgentRunDetail)
    async def get_run(
        run_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> AgentRunDetail:
        run_store = run_store_for(current_user.user_id)
        try:
            run = await run_store.get_run_detail(
                user_id=current_user.user_id,
                run_id=run_id,
            )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        if run is None:
            raise not_found()
        return run

    @router.get("/{run_id}/events", response_model=RunEventListResponse)
    async def list_run_events(
        run_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
        since_event_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ) -> RunEventListResponse:
        run_store = run_store_for(current_user.user_id)
        try:
            run = await run_store.get_run(
                user_id=current_user.user_id,
                run_id=run_id,
            )
            if run is None:
                raise not_found()
            events = await run_store.list_events(
                user_id=current_user.user_id,
                run_id=run_id,
                since_event_id=since_event_id,
                limit=limit,
            )
        except HTTPException:
            raise
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        return RunEventListResponse(events=events)

    @router.get("/events/stream")
    async def stream_all_events(
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
        since_event_id: str | None = None,
    ) -> StreamingResponse:
        run_store = run_store_for(current_user.user_id)
        return StreamingResponse(
            event_stream(
                stream_service=RunEventStreamService(run_store=run_store),
                user_id=current_user.user_id,
                run_id=None,
                since_event_id=since_event_id,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    @router.get("/{run_id}/events/stream")
    async def stream_run_events(
        run_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
        since_event_id: str | None = None,
    ) -> StreamingResponse:
        run_store = run_store_for(current_user.user_id)
        try:
            run = await run_store.get_run(
                user_id=current_user.user_id,
                run_id=run_id,
            )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        if run is None:
            raise not_found()
        return StreamingResponse(
            event_stream(
                stream_service=RunEventStreamService(run_store=run_store),
                user_id=current_user.user_id,
                run_id=run_id,
                since_event_id=since_event_id,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    @router.post("/{run_id}/cancel", response_model=AgentRunDetail)
    async def cancel_run(
        run_id: str,
        request_body: RunCancelRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> AgentRunDetail:
        run_store = run_store_for(current_user.user_id)
        try:
            run = await run_store.request_cancel(
                run_id=run_id,
                user_id=current_user.user_id,
                reason=request_body.reason,
            )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        if run is None:
            raise not_found()
        detail = await run_store.get_run_detail(
            user_id=current_user.user_id,
            run_id=run_id,
        )
        if detail is None:
            raise not_found()
        return detail

    @router.post("/{run_id}/retry", response_model=AgentRunDetail)
    async def retry_run(
        run_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> AgentRunDetail:
        run_store = run_store_for(current_user.user_id)
        try:
            run = await run_store.retry_run(
                run_id=run_id,
                user_id=current_user.user_id,
            )
        except AgentRunTransitionError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        if run is None:
            raise not_found()
        detail = await run_store.get_run_detail(
            user_id=current_user.user_id,
            run_id=run_id,
        )
        if detail is None:
            raise not_found()
        return detail

    return router


async def event_stream(
    *,
    stream_service: RunEventStreamService,
    user_id: str,
    run_id: str | None,
    since_event_id: str | None,
) -> AsyncIterator[bytes]:
    async for event in stream_service.stream_events(
        user_id=user_id,
        run_id=run_id,
        since_event_id=since_event_id,
    ):
        yield sse_frame(
            event_id=event.id,
            event_name=event.kind.value,
            payload=event.model_dump(mode="json"),
        )


def sse_frame(
    *,
    event_id: str,
    event_name: str,
    payload: dict[str, object],
) -> bytes:
    encoded = orjson.dumps(payload).decode()
    return f"id: {event_id}\nevent: {event_name}\ndata: {encoded}\n\n".encode()


def not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=RUN_NOT_FOUND)


def unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=RUNS_UNAVAILABLE,
    )

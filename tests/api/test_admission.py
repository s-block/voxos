import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from starlette.responses import StreamingResponse

from voxos.api.admission import AdmissionAuthDependency, ApiAdmissionController, ApiAdmissionMiddleware
from voxos.api.settings import ApiSettings
from voxos.auth.models import AuthenticatedUser


@dataclass(slots=True)
class Clock:
    now: float = 0

    def __call__(self) -> float:
        return self.now


async def authenticated_user(request: Request) -> AuthenticatedUser:
    # This stand-in represents the result of the real verified Firebase dependency.
    user_id = request.headers.get("X-Test-Verified-User", "owner")
    return AuthenticatedUser(user_id=user_id, firebase_user_id=f"firebase-{user_id}")


def admission_app(settings: ApiSettings, clock: Clock) -> tuple[FastAPI, ApiAdmissionController]:
    app = FastAPI()
    controller = ApiAdmissionController(settings, clock)
    auth = AdmissionAuthDependency(dependency=authenticated_user, controller=controller)
    app.add_middleware(ApiAdmissionMiddleware, controller=controller)

    @app.post("/work")
    async def work(user: Annotated[AuthenticatedUser, Depends(auth)]) -> dict[str, str]:
        return {"owner": user.user_id}

    @app.post("/failure")
    async def failure(user: Annotated[AuthenticatedUser, Depends(auth)]) -> None:
        raise HTTPException(502, "Failed operation")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app, controller


@pytest.mark.asyncio
async def test_user_budget_uses_verified_identity_and_refills() -> None:
    clock = Clock()
    app, controller = admission_app(
        ApiSettings(_env_file=None, api_rate_limit_burst=1, api_rate_limit_per_minute=60), clock
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/work", json={"user_id": "other"})).status_code == 200
        rejected = await client.post("/work", json={"user_id": "different"})
        assert rejected.status_code == 429
        assert rejected.headers["Retry-After"] == "1"
        other = await client.post("/work", headers={"X-Test-Verified-User": "other"})
        assert other.status_code == 200
        assert other.json() == {"owner": "other"}
        clock.now = 1
        assert (await client.post("/work")).status_code == 200


@pytest.mark.asyncio
async def test_failed_operations_release_user_and_global_slots() -> None:
    app, controller = admission_app(ApiSettings(_env_file=None, api_max_in_flight=1, api_max_user_in_flight=1), Clock())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/failure")).status_code == 502
        assert (await client.post("/work")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("global_limit,expected", [(1, 503), (2, 429)])
async def test_streams_hold_capacity_until_closed_but_health_stays_available(global_limit: int, expected: int) -> None:
    clock = Clock()
    app, controller = admission_app(
        ApiSettings(_env_file=None, api_max_in_flight=global_limit, api_max_user_in_flight=1), clock
    )
    started = asyncio.Event()
    release = asyncio.Event()
    auth = AdmissionAuthDependency(dependency=authenticated_user, controller=controller)

    @app.get("/events/stream")
    async def stream(user: Annotated[AuthenticatedUser, Depends(auth)]) -> StreamingResponse:
        async def content() -> AsyncIterator[bytes]:
            started.set()
            yield b"event: ready\n\n"
            await release.wait()

        return StreamingResponse(content(), media_type="text/event-stream")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        execution = asyncio.create_task(client.get("/events/stream"))
        await asyncio.wait_for(started.wait(), timeout=2)
        assert (await client.post("/work")).status_code == expected
        assert (await client.get("/health")).status_code == 200
        release.set()
        assert (await execution).status_code == 200
        assert (await client.post("/work")).status_code == 200


@pytest.mark.asyncio
async def test_cancellation_releases_capacity() -> None:
    app, controller = admission_app(ApiSettings(_env_file=None, api_max_in_flight=1), Clock())
    started = asyncio.Event()

    @app.get("/slow")
    async def slow() -> None:
        started.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        execution = asyncio.create_task(client.get("/slow"))
        await asyncio.wait_for(started.wait(), timeout=2)
        assert (await client.get("/work")).status_code == 503
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution
        assert (await client.post("/work")).status_code == 200

from dataclasses import dataclass
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.run_models import AgentRunKind
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.api.runs import create_runs_router
from voxos.auth.models import AuthenticatedUser
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory


@dataclass(frozen=True, slots=True)
class StaticAuthDependency:
    authenticated_user: AuthenticatedUser

    async def __call__(self, request: Request) -> AuthenticatedUser:
        return self.authenticated_user


def authenticated_user(user_id: str = "local-user-123") -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user_id,
        firebase_user_id=f"firebase-{user_id}",
        email="user@example.com",
        email_verified=True,
    )


def create_test_client(
    *,
    sqlite_session_factory: AsyncSessionFactory,
    user_id: str = "local-user-123",
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_runs_router(
            auth_dependency=StaticAuthDependency(authenticated_user(user_id)),
            session_factory=sqlite_session_factory,
        ),
    )
    return TestClient(app)


@pytest.mark.asyncio
async def test_runs_api_lists_and_cancels_only_current_users_runs(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(session_factory=sqlite_session_factory)
    owner_agent = await identity_service.get_or_create_default_agent("owner-user")
    other_agent = await identity_service.get_or_create_default_agent("other-user")
    store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    owner_run = await store.enqueue_run(
        user_id="owner-user",
        agent_id=owner_agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Owner"},
        created_by_user_id="owner-user",
    )
    other_run = await store.enqueue_run(
        user_id="other-user",
        agent_id=other_agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Other"},
        created_by_user_id="other-user",
    )
    owner_client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_id="owner-user",
    )

    list_response = owner_client.get("/api/runs")
    cancel_response = owner_client.post(
        f"/api/runs/{owner_run.id}/cancel",
        json={"reason": "Stop"},
    )
    wrong_cancel = owner_client.post(
        f"/api/runs/{other_run.id}/cancel",
        json={"reason": "Stop"},
    )

    assert list_response.status_code == 200
    assert [run["id"] for run in list_response.json()["runs"]] == [owner_run.id]
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"
    assert wrong_cancel.status_code == 404


@pytest.mark.asyncio
async def test_runs_api_retries_failed_runs_and_returns_events(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(session_factory=sqlite_session_factory)
    agent = await identity_service.get_or_create_default_agent("owner-user")
    store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    run = await store.enqueue_run(
        user_id="owner-user",
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Retry"},
        created_by_user_id="owner-user",
    )
    await store.start_run(
        run_id=run.id,
        user_id="owner-user",
        lease_owner="worker-1",
        lease_expires_at=utc_now() + timedelta(seconds=60),
    )
    await store.fail_run(
        run_id=run.id,
        user_id="owner-user",
        error_type="RuntimeError",
        error_message="Failed",
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_id="owner-user",
    )

    retry_response = client.post(f"/api/runs/{run.id}/retry")
    events_response = client.get(f"/api/runs/{run.id}/events")

    assert retry_response.status_code == 200
    assert retry_response.json()["status"] == "queued"
    assert events_response.status_code == 200
    assert [event["kind"] for event in events_response.json()["events"]] == [
        "run_enqueued",
        "run_started",
        "run_failed",
        "run_retry_scheduled",
    ]

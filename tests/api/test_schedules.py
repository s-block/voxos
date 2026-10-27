from dataclasses import dataclass
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.storage import SqlModelAgentRuntimeStore
from voxos.api.schedules import create_schedules_router
from voxos.auth.models import AuthenticatedUser
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.text.run_chat_service import RunChatService


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
        create_schedules_router(
            auth_dependency=StaticAuthDependency(authenticated_user(user_id)),
            session_factory=sqlite_session_factory,
        ),
    )
    return TestClient(app)


@pytest.mark.asyncio
async def test_schedules_api_crud_pause_resume_and_run_now(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("owner-user")
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_id="owner-user",
    )

    create_response = client.post(
        "/api/schedules",
        json={
            "agent_id": agent.id,
            "name": "Daily summary",
            "kind": "one_shot",
            "run_at": (utc_now() + timedelta(minutes=10)).isoformat(),
            "payload_json": {"text": "Summarize my day"},
        },
    )
    schedule = create_response.json()
    list_response = client.get("/api/schedules")
    pause_response = client.post(f"/api/schedules/{schedule['id']}/pause")
    resume_response = client.post(f"/api/schedules/{schedule['id']}/resume")
    run_now_response = client.post(f"/api/schedules/{schedule['id']}/run-now")
    delete_response = client.delete(f"/api/schedules/{schedule['id']}")
    get_deleted_response = client.get(f"/api/schedules/{schedule['id']}")

    assert create_response.status_code == 201
    assert schedule["name"] == "Daily summary"
    assert schedule["chat_id"] is not None
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()["schedules"]] == [
        schedule["id"],
    ]
    assert pause_response.status_code == 200
    assert pause_response.json()["status"] == "paused"
    assert resume_response.status_code == 200
    assert resume_response.json()["status"] == "active"
    assert run_now_response.status_code == 200
    assert run_now_response.json()["last_run_status"] == "queued"
    assert delete_response.status_code == 204
    assert get_deleted_response.status_code == 404


@pytest.mark.asyncio
async def test_schedules_api_enforces_agent_and_schedule_ownership(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    owner_agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("owner-user")
    other_client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_id="other-user",
    )
    owner_client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_id="owner-user",
    )
    create_response = owner_client.post(
        "/api/schedules",
        json={
            "agent_id": owner_agent.id,
            "name": "Owner schedule",
            "kind": "interval",
            "interval_seconds": 300,
            "payload_json": {"text": "Check"},
        },
    )
    schedule = create_response.json()

    wrong_agent_response = other_client.post(
        "/api/schedules",
        json={
            "agent_id": owner_agent.id,
            "name": "Wrong agent",
            "kind": "interval",
            "interval_seconds": 300,
            "payload_json": {"text": "Check"},
        },
    )
    wrong_schedule_response = other_client.post(
        f"/api/schedules/{schedule['id']}/pause",
    )

    assert create_response.status_code == 201
    assert wrong_agent_response.status_code == 404
    assert wrong_schedule_response.status_code == 404


@pytest.mark.asyncio
async def test_schedules_api_rejects_another_users_chat(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(session_factory=sqlite_session_factory)
    owner_agent = await identity_service.get_or_create_default_agent("owner-user")
    other_agent = await identity_service.get_or_create_default_agent("other-user")
    run_store = SqlModelAgentRunStore(sqlite_session_factory)
    other_chat = await RunChatService(
        session_factory=sqlite_session_factory,
        runtime_store=SqlModelAgentRuntimeStore(sqlite_session_factory),
        run_store=run_store,
    ).create_chat(
        user_id="other-user",
        agent_id=other_agent.id,
        title="Other chat",
    )
    owner_client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_id="owner-user",
    )

    response = owner_client.post(
        "/api/schedules",
        json={
            "agent_id": owner_agent.id,
            "chat_id": other_chat.id,
            "name": "Wrong destination",
            "kind": "interval",
            "interval_seconds": 300,
            "payload_json": {"text": "Check"},
        },
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Chat was not found"}


@pytest.mark.asyncio
async def test_schedules_api_rejects_resume_and_run_now_after_chat_deletion(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    user_id = "owner-user"
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent(user_id)
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_id=user_id,
    )
    create_response = client.post(
        "/api/schedules",
        json={
            "agent_id": agent.id,
            "name": "Deleted destination",
            "kind": "interval",
            "interval_seconds": 300,
            "payload_json": {"text": "Check"},
        },
    )
    schedule = create_response.json()
    await SqlModelAgentRuntimeStore(sqlite_session_factory).soft_delete_session(
        session_id=schedule["chat_id"],
        user_id=user_id,
        agent_id=agent.id,
    )

    resume_response = client.post(f"/api/schedules/{schedule['id']}/resume")
    run_now_response = client.post(f"/api/schedules/{schedule['id']}/run-now")

    assert resume_response.status_code == 404
    assert resume_response.json() == {"detail": "Chat was not found"}
    assert run_now_response.status_code == 404
    assert run_now_response.json() == {"detail": "Chat was not found"}

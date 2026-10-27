from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from voxos.agent_core.mcp_tool_cache import McpToolCatalogCache
from voxos.api.agents import create_agents_router
from voxos.auth.models import AuthenticatedUser
from voxos.db.session import AsyncSessionFactory
from voxos.mcp.records import UserMcpServerRecord


@dataclass(frozen=True, slots=True)
class StaticAuthDependency:
    authenticated_user: AuthenticatedUser

    async def __call__(self, request: Request) -> AuthenticatedUser:
        return self.authenticated_user


class RecordingMcpToolCatalogCache(McpToolCatalogCache):
    def __init__(self) -> None:
        super().__init__(max_entries=10, ttl_seconds=60)
        self.invalidated_users: list[str] = []

    async def invalidate_user(self, user_id: str) -> int:
        self.invalidated_users.append(user_id)
        return await super().invalidate_user(user_id)


def authenticated_user(user_id: str = "local-user-123") -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user_id,
        firebase_user_id=f"firebase-{user_id}",
        email="user@example.com",
        email_verified=True,
    )


@pytest.mark.asyncio
async def test_agents_api_creates_and_updates_default_agent(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    app = FastAPI()
    app.include_router(
        create_agents_router(
            auth_dependency=StaticAuthDependency(authenticated_user()),
            session_factory=sqlite_session_factory,
        ),
    )
    client = TestClient(app)

    response = client.get("/api/agents/default")

    assert response.status_code == 200
    agent = response.json()
    assert agent["owner_user_id"] == "local-user-123"
    assert agent["is_default"] is True

    update_response = client.patch(
        f"/api/agents/{agent['id']}",
        json={
            "display_name": "Desk agent",
            "description": "Handles calls.",
        },
    )

    assert update_response.status_code == 200
    assert update_response.json()["display_name"] == "Desk agent"


@pytest.mark.asyncio
async def test_agents_api_returns_not_found_for_other_users_agent(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    owner_app = FastAPI()
    owner_app.include_router(
        create_agents_router(
            auth_dependency=StaticAuthDependency(authenticated_user("owner-user")),
            session_factory=sqlite_session_factory,
        ),
    )
    owner_client = TestClient(owner_app)
    owner_agent = owner_client.get("/api/agents/default").json()

    other_app = FastAPI()
    other_app.include_router(
        create_agents_router(
            auth_dependency=StaticAuthDependency(authenticated_user("other-user")),
            session_factory=sqlite_session_factory,
        ),
    )
    other_client = TestClient(other_app)

    response = other_client.patch(
        f"/api/agents/{owner_agent['id']}",
        json={"display_name": "Wrong user"},
    )
    route_response = other_client.get(
        f"/api/agents/{owner_agent['id']}/tool-routes",
    )
    route_update_response = other_client.put(
        f"/api/agents/{owner_agent['id']}/tool-routes",
        json={
            "preferences": [
                {"action": "web.search", "mode": "automatic"},
            ],
        },
    )

    assert response.status_code == 404
    assert route_response.status_code == 404
    assert route_update_response.status_code == 404


@pytest.mark.asyncio
async def test_agents_api_persists_tool_grants(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    app = FastAPI()
    app.include_router(
        create_agents_router(
            auth_dependency=StaticAuthDependency(authenticated_user()),
            session_factory=sqlite_session_factory,
        ),
    )
    client = TestClient(app)
    agent = client.get("/api/agents/default").json()

    response = client.put(
        f"/api/agents/{agent['id']}/tool-grants",
        json={
            "grants": [
                {
                    "tool_name": "list_agent_memories",
                    "tool_source": "internal",
                    "status": "allowed",
                    "risk": "low",
                },
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["grants"][0]["tool_name"] == "list_agent_memories"
    assert body["grants"][0]["status"] == "allowed"
    assert any(tool["tool_name"] == "list_agent_memories" for tool in body["internal_tools"])


@pytest.mark.asyncio
async def test_agents_api_manages_action_level_tool_routes(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    cache = RecordingMcpToolCatalogCache()
    app = FastAPI()
    app.include_router(
        create_agents_router(
            auth_dependency=StaticAuthDependency(authenticated_user()),
            session_factory=sqlite_session_factory,
            mcp_tool_catalog_cache=cache,
        ),
    )
    client = TestClient(app)
    agent = client.get("/api/agents/default").json()
    exa_server = UserMcpServerRecord(
        user_id="local-user-123",
        label="Exa",
        transport="streamable_http",
        url="https://mcp.exa.ai/mcp",
        auth_type="none",
        enabled=True,
        trust_state="trusted",
        status="connected",
        safe_metadata={"preset_id": "exa"},
        allowed_tools=["web_search_exa"],
    )
    browserless_server = UserMcpServerRecord(
        user_id="local-user-123",
        label="Browserless",
        transport="streamable_http",
        url="https://mcp.browserless.io/mcp",
        auth_type="none",
        enabled=True,
        trust_state="trusted",
        status="connected",
        safe_metadata={"preset_id": "browserless"},
        allowed_tools=["browserless_search"],
    )
    async with sqlite_session_factory() as session:
        session.add(exa_server)
        session.add(browserless_server)
        await session.commit()
        await session.refresh(exa_server)
        await session.refresh(browserless_server)
    grant_response = client.put(
        f"/api/agents/{agent['id']}/tool-grants",
        json={
            "grants": [
                {
                    "tool_name": "web_search_exa",
                    "tool_source": "mcp_server",
                    "mcp_server_id": exa_server.id,
                    "status": "allowed",
                    "risk": "low",
                },
                {
                    "tool_name": "browserless_search",
                    "tool_source": "mcp_server",
                    "mcp_server_id": browserless_server.id,
                    "status": "allowed",
                    "risk": "medium",
                },
            ],
        },
    )
    assert grant_response.status_code == 200
    assert cache.invalidated_users == ["local-user-123"]

    response = client.get(f"/api/agents/{agent['id']}/tool-routes")

    assert response.status_code == 200
    body = response.json()
    assert body["definitions"][0]["action"] == "web.search"
    assert [candidate["provider_label"] for candidate in body["candidates"]] == [
        "Exa",
        "Browserless",
    ]
    update_response = client.put(
        f"/api/agents/{agent['id']}/tool-routes",
        json={
            "preferences": [
                {
                    "action": "web.search",
                    "mode": "pinned",
                    "pinned_tool": {
                        "tool_name": "browserless_search",
                        "tool_source": "mcp_server",
                        "mcp_server_id": browserless_server.id,
                    },
                },
            ],
        },
    )
    assert cache.invalidated_users == ["local-user-123", "local-user-123"]

    assert update_response.status_code == 200
    preference = update_response.json()["preferences"][0]
    assert preference["mode"] == "pinned"
    assert preference["pinned_tool"]["tool_name"] == "browserless_search"


@pytest.mark.asyncio
async def test_agents_api_manages_memories(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    app = FastAPI()
    app.include_router(
        create_agents_router(
            auth_dependency=StaticAuthDependency(authenticated_user()),
            session_factory=sqlite_session_factory,
        ),
    )
    client = TestClient(app)

    create_response = client.post(
        "/api/agents/memories",
        json={
            "kind": "preference",
            "text": "Prefers short answers.",
            "sensitivity": "low",
            "confirmed": True,
        },
    )

    assert create_response.status_code == 200
    memory = create_response.json()
    assert memory["status"] == "active"

    update_response = client.patch(
        f"/api/agents/memories/{memory['id']}",
        json={"text": "Prefers concise answers.", "status": "active"},
    )

    assert update_response.status_code == 200
    assert update_response.json()["text"] == "Prefers concise answers."

    list_response = client.get("/api/agents/memories?status=active")

    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()["memories"]] == [memory["id"]]

    archive_response = client.delete(f"/api/agents/memories/{memory['id']}")

    assert archive_response.status_code == 200
    assert archive_response.json()["status"] == "archived"

    delete_response = client.delete(f"/api/agents/memories/{memory['id']}/permanent")

    assert delete_response.status_code == 204

    deleted_list_response = client.get("/api/agents/memories")

    assert deleted_list_response.status_code == 200
    assert deleted_list_response.json()["memories"] == []


@pytest.mark.asyncio
async def test_agents_api_manages_user_skills(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    app = FastAPI()
    app.include_router(
        create_agents_router(
            auth_dependency=StaticAuthDependency(authenticated_user()),
            session_factory=sqlite_session_factory,
        ),
    )
    client = TestClient(app)

    create_response = client.post(
        "/api/agents/skills",
        json={
            "name": "Release Checklist",
            "slug": "release-checklist",
            "category": "planning",
            "description": "Steps for release reviews.",
            "body_markdown": "Check migrations and deployment safety.",
        },
    )

    assert create_response.status_code == 200
    assert create_response.json()["slug"] == "release-checklist"

    update_response = client.patch(
        "/api/agents/skills/release-checklist",
        json={"description": "Steps for production release reviews."},
    )

    assert update_response.status_code == 200
    assert update_response.json()["description"] == "Steps for production release reviews."

    list_response = client.get("/api/agents/skills?status=active")

    assert list_response.status_code == 200
    assert [skill["slug"] for skill in list_response.json()["skills"]] == [
        "release-checklist",
    ]

    archive_response = client.delete("/api/agents/skills/release-checklist")

    assert archive_response.status_code == 200
    assert archive_response.json()["status"] == "archived"

    delete_response = client.delete("/api/agents/skills/release-checklist/permanent")

    assert delete_response.status_code == 204

    deleted_list_response = client.get("/api/agents/skills")

    assert deleted_list_response.status_code == 200
    assert deleted_list_response.json()["skills"] == []


@pytest.mark.asyncio
async def test_agents_api_manages_task_plans(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    app = FastAPI()
    app.include_router(
        create_agents_router(
            auth_dependency=StaticAuthDependency(authenticated_user()),
            session_factory=sqlite_session_factory,
        ),
    )
    client = TestClient(app)

    create_response = client.post(
        "/api/agents/task-plans",
        json={
            "objective": "Ship agent data manager",
            "steps": ["Add API", "Add UI"],
            "acceptance_criteria": ["Targeted tests pass"],
            "priority": "high",
        },
    )

    assert create_response.status_code == 200
    plan = create_response.json()
    assert plan["status"] == "active"
    assert len(plan["steps"]) == 2

    update_response = client.patch(
        f"/api/agents/task-plans/{plan['id']}",
        json={"objective": "Ship agent data management UX", "priority": "normal"},
    )

    assert update_response.status_code == 200
    assert update_response.json()["objective"] == "Ship agent data management UX"
    assert update_response.json()["priority"] == "normal"

    step_response = client.patch(
        f"/api/agents/task-steps/{plan['steps'][0]['id']}",
        json={"status": "completed", "note": "API is covered."},
    )

    assert step_response.status_code == 200
    assert step_response.json()["steps"][0]["status"] == "completed"

    archive_response = client.delete(f"/api/agents/task-plans/{plan['id']}")

    assert archive_response.status_code == 200
    assert archive_response.json()["status"] == "archived"

    delete_response = client.delete(f"/api/agents/task-plans/{plan['id']}/permanent")

    assert delete_response.status_code == 204

    deleted_list_response = client.get("/api/agents/task-plans")

    assert deleted_list_response.status_code == 200
    assert deleted_list_response.json()["task_plans"] == []

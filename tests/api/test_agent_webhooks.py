from dataclasses import dataclass

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from voxos.agent_identity.services import AgentIdentityService
from voxos.api.agent_webhooks import (
    MAX_WEBHOOK_BODY_BYTES,
    create_agent_webhooks_router,
    webhook_token,
)
from voxos.api.runs import create_runs_router
from voxos.auth.models import AuthenticatedUser
from voxos.db.session import AsyncSessionFactory


@dataclass(frozen=True, slots=True)
class StaticAuthDependency:
    authenticated_user: AuthenticatedUser

    async def __call__(self, request: Request) -> AuthenticatedUser:
        return self.authenticated_user


def authenticated_user(user_id: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user_id,
        firebase_user_id=f"firebase-{user_id}",
        email="webhooks@example.com",
        email_verified=True,
    )


def create_test_client(
    *,
    sqlite_session_factory: AsyncSessionFactory,
    user_id: str,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_agent_webhooks_router(
            auth_dependency=StaticAuthDependency(authenticated_user(user_id)),
            session_factory=sqlite_session_factory,
            public_base_url="https://voxos.example",
        ),
    )
    app.include_router(
        create_runs_router(
            auth_dependency=StaticAuthDependency(authenticated_user(user_id)),
            session_factory=sqlite_session_factory,
        ),
    )
    return TestClient(app)


@pytest.mark.asyncio
async def test_agent_webhook_api_crud_delivery_and_idempotency(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    user_id = "webhook-owner"
    agent = await AgentIdentityService(
        sqlite_session_factory,
    ).get_or_create_default_agent(user_id)
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_id=user_id,
    )

    create_response = client.post(
        "/api/agent-webhooks",
        json={
            "agent_id": agent.id,
            "name": "CRM lead",
            "instruction": "Qualify the incoming lead.",
        },
    )
    created = create_response.json()

    assert create_response.status_code == 201
    assert create_response.headers["cache-control"] == "no-store"
    assert created["trigger_url"].startswith("https://voxos.example/api/agent-webhooks/")
    token = created["token"]
    delivery_path = created["trigger_path"]

    listed = client.get("/api/agent-webhooks")
    assert listed.status_code == 200
    assert "token" not in listed.json()["webhooks"][0]

    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": "crm-event-123",
    }
    first_delivery = client.post(
        delivery_path,
        headers=headers,
        json={"lead": "Ada", "user_id": "untrusted-user"},
    )
    retry_delivery = client.post(
        delivery_path,
        headers=headers,
        json={"lead": "Changed retry body"},
    )

    assert first_delivery.status_code == 202
    assert retry_delivery.status_code == 202
    assert retry_delivery.json()["run_id"] == first_delivery.json()["run_id"]

    run_response = client.get(f"/api/runs/{first_delivery.json()['run_id']}")
    run = run_response.json()
    assert run_response.status_code == 200
    assert run["kind"] == "webhook_agent_task"
    assert run["webhook_id"] == created["id"]
    assert run["chat_id"] is None
    assert "untrusted-user" in run["payload_json"]["text"]

    patched = client.patch(
        f"/api/agent-webhooks/{created['id']}",
        json={"enabled": False},
    )
    disabled_delivery = client.post(delivery_path, headers=headers, json={"lead": "Grace"})
    deleted = client.delete(f"/api/agent-webhooks/{created['id']}")

    assert patched.status_code == 200
    assert patched.json()["enabled"] is False
    assert disabled_delivery.status_code == 404
    assert deleted.status_code == 204
    assert client.get(f"/api/agent-webhooks/{created['id']}").status_code == 404


@pytest.mark.asyncio
async def test_agent_webhook_delivery_validates_auth_before_body_and_rejects_bad_input(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    user_id = "webhook-owner"
    agent = await AgentIdentityService(
        sqlite_session_factory,
    ).get_or_create_default_agent(user_id)
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_id=user_id,
    )
    created = client.post(
        "/api/agent-webhooks",
        json={
            "agent_id": agent.id,
            "name": "Input validation",
            "instruction": "Inspect the event.",
        },
    ).json()
    path = created["trigger_path"]
    token = created["token"]
    valid_headers = {"Authorization": f"Bearer {token}"}

    missing_auth = client.post(
        path,
        content=b"x" * (MAX_WEBHOOK_BODY_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )
    query_token = client.post(f"{path}?token={token}", json={})
    wrong_token = client.post(path, headers={"Authorization": "Bearer wrong"}, json={})
    both_token_headers = client.post(
        path,
        headers={
            "Authorization": f"Bearer {token}",
            "X-Voxos-Webhook-Token": token,
        },
        json={},
    )
    duplicated_token_header = client.post(
        path,
        headers=[
            ("Authorization", f"Bearer {token}"),
            ("Authorization", f"Bearer {token}"),
        ],
        json={},
    )
    wrong_media = client.post(path, headers=valid_headers, content=b"{}")
    non_object = client.post(path, headers=valid_headers, json=[])
    invalid_json = client.post(
        path,
        headers=valid_headers | {"Content-Type": "application/json"},
        content=b"{",
    )
    oversized = client.post(
        path,
        headers=valid_headers | {"Content-Type": "application/json"},
        content=b"{" + b" " * MAX_WEBHOOK_BODY_BYTES + b"}",
    )

    assert missing_auth.status_code == 401
    assert query_token.status_code == 401
    assert wrong_token.status_code == 401
    assert both_token_headers.status_code == 401
    assert duplicated_token_header.status_code == 401
    assert wrong_media.status_code == 415
    assert non_object.status_code == 422
    assert invalid_json.status_code == 422
    assert oversized.status_code == 413

    non_ascii_request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [(b"authorization", b"Bearer \xff")],
            "query_string": b"",
        },
    )
    with pytest.raises(HTTPException, match="Webhook token was rejected"):
        webhook_token(non_ascii_request)


@pytest.mark.asyncio
async def test_agent_webhook_token_rotation_invalidates_the_previous_token(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    user_id = "webhook-owner"
    agent = await AgentIdentityService(
        sqlite_session_factory,
    ).get_or_create_default_agent(user_id)
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_id=user_id,
    )
    created = client.post(
        "/api/agent-webhooks",
        json={
            "agent_id": agent.id,
            "name": "Rotate me",
            "instruction": "Handle this event.",
        },
    ).json()

    rotated_response = client.post(f"/api/agent-webhooks/{created['id']}/rotate-token")
    rotated = rotated_response.json()

    assert rotated_response.status_code == 200
    assert rotated_response.headers["cache-control"] == "no-store"
    assert rotated["token"] != created["token"]
    assert (
        client.post(
            created["trigger_path"],
            headers={"Authorization": f"Bearer {created['token']}"},
            json={},
        ).status_code
        == 401
    )
    assert (
        client.post(
            created["trigger_path"],
            headers={"X-Voxos-Webhook-Token": rotated["token"]},
            json={},
        ).status_code
        == 202
    )

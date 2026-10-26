from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import select
from starlette.requests import Request

from voxos.auth.models import AuthenticatedUser
from voxos.db.session import AsyncSessionFactory
from voxos.notifications.models import PushInstallationRecord
from voxos.notifications.router import create_push_notifications_router


@dataclass(frozen=True, slots=True)
class StaticAuthDependency:
    user_id: str

    async def __call__(self, _: Request) -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=self.user_id,
            firebase_user_id=f"firebase-{self.user_id}",
        )


def test_push_installation_registration_rotation_and_revocation(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    app = FastAPI()
    app.include_router(
        create_push_notifications_router(
            auth_dependency=StaticAuthDependency("owner-user"),
            session_factory=sqlite_session_factory,
        ),
    )
    client = TestClient(app)

    initial = client.post(
        "/api/push/installations/register",
        json={
            "installation_id": "fid-123",
            "registration_token": "token-1",  # pragma: allowlist secret
            "platform": "ios",
        },
    )
    rotated = client.post(
        "/api/push/installations/register",
        json={
            "installation_id": "fid-123",
            "registration_token": "token-2",  # pragma: allowlist secret
            "platform": "ios",
        },
    )
    revoked = client.post(
        "/api/push/installations/revoke",
        json={"installation_id": "fid-123"},
    )

    assert initial.status_code == 204
    assert rotated.status_code == 204
    assert revoked.status_code == 204


@pytest.mark.asyncio
async def test_push_installation_uses_authenticated_local_user_id(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    app = FastAPI()
    app.include_router(
        create_push_notifications_router(
            auth_dependency=StaticAuthDependency("local-owner"),
            session_factory=sqlite_session_factory,
        ),
    )
    response = TestClient(app).post(
        "/api/push/installations/register",
        json={
            "installation_id": "fid-local-owner",
            "registration_token": "token-local-owner",  # pragma: allowlist secret
            "platform": "android",
        },
    )

    assert response.status_code == 204
    async with sqlite_session_factory() as session:
        records = (await session.exec(select(PushInstallationRecord))).all()
    assert len(records) == 1
    assert records[0].user_id == "local-owner"

from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from voxos.api.codex_cloud import create_codex_cloud_router
from voxos.auth.models import AuthenticatedUser
from voxos.codex_cloud.settings import CodexCloudSettings
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory


@dataclass(frozen=True, slots=True)
class StaticAuthDependency:
    user_id: str

    async def __call__(self, request: Request) -> AuthenticatedUser:
        del request
        return AuthenticatedUser(
            user_id=self.user_id,
            firebase_user_id=f"firebase-{self.user_id}",
            email=f"{self.user_id}@example.com",
            email_verified=True,
        )


def test_codex_cloud_login_sessions_are_tenant_scoped(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    owner = _client(
        user_id="owner",
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    other = _client(
        user_id="other",
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )

    created = owner.post("/api/codex-cloud/account/login")
    auth_session_id = created.json()["id"]

    assert created.status_code == 202
    assert created.headers["cache-control"] == "no-store"
    assert owner.get(f"/api/codex-cloud/auth-sessions/{auth_session_id}").status_code == 200
    assert other.get(f"/api/codex-cloud/auth-sessions/{auth_session_id}").status_code == 404
    owner.close()
    other.close()


def _client(
    *,
    user_id: str,
    session_factory: AsyncSessionFactory,
    vault: UserSecretVaultService,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_codex_cloud_router(
            auth_dependency=StaticAuthDependency(user_id),
            session_factory=session_factory,
            secret_vault=vault,
            settings=CodexCloudSettings(),
        ),
    )
    return TestClient(app)

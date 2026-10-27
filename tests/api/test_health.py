import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from voxos.api.main import create_app
from voxos.api.settings import ApiSettings
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory


@pytest.mark.parametrize("revision", [None, "a" * 40])
def test_health_preserves_body_and_reports_configured_revision(
    revision: str | None,
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    app = create_app(
        api_settings=ApiSettings(_env_file=None, deployment_revision=revision),
        secret_vault=user_secret_vault,
        session_factory=sqlite_session_factory,
    )
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers.get("X-Voxos-Revision") == revision


def test_deployment_revision_cannot_inject_response_headers() -> None:
    with pytest.raises(ValidationError):
        ApiSettings(_env_file=None, deployment_revision="revision\r\nInjected: true")


def test_api_documentation_can_be_disabled(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    app = create_app(
        api_settings=ApiSettings(_env_file=None, api_docs_enabled=False),
        secret_vault=user_secret_vault,
        session_factory=sqlite_session_factory,
    )
    with TestClient(app) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404
        assert client.get("/health").status_code == 200

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from voxos.api.client_config import (
    ClientConfigSettings,
    create_client_config_router,
)
from voxos.api.main import create_app
from voxos.auth.models import AuthenticatedUser
from voxos.auth.settings import FirebaseAuthSettings
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.voice.settings import VoiceAgentSettings

TEST_FIREBASE_API_KEY = "public-api-key"  # pragma: allowlist secret


async def unexpected_auth_dependency(_: Request) -> AuthenticatedUser:
    raise AssertionError


def configured_client_settings() -> ClientConfigSettings:
    return ClientConfigSettings(
        firebase_api_key=TEST_FIREBASE_API_KEY,
        firebase_messaging_sender_id="123456789",
        firebase_android_app_id="android-app-id",
        firebase_ios_app_id="ios-app-id",
        firebase_web_app_id="web-app-id",
        firebase_google_ios_client_id="google-ios-client-id",
        firebase_google_server_client_id="google-server-client-id",
        firebase_auth_domain="voxos.example.test",
        firebase_storage_bucket="voxos.example.test",
    )


def test_client_config_is_public_and_wired_into_application(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    app = create_app(
        auth_dependency=unexpected_auth_dependency,
        client_config_settings=configured_client_settings(),
        firebase_settings=FirebaseAuthSettings(
            firebase_project_id="voxos-project",
        ),
        secret_vault=user_secret_vault,
        session_factory=sqlite_session_factory,
        voice_settings=VoiceAgentSettings(
            voice_agent_name="voxos-mobile-agent",
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/client-config")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "firebase": {
            "project_id": "voxos-project",
            "api_key": TEST_FIREBASE_API_KEY,
            "messaging_sender_id": "123456789",
            "android_app_id": "android-app-id",
            "ios_app_id": "ios-app-id",
            "web_app_id": "web-app-id",
            "google_ios_client_id": "google-ios-client-id",
            "google_server_client_id": "google-server-client-id",
            "auth_domain": "voxos.example.test",
            "storage_bucket": "voxos.example.test",
        },
        "push": {"enabled": False},
        "voice": {
            "agent_name": "voxos-mobile-agent",
            "token_endpoint_path": "/api/livekit/token",
        },
    }


def test_client_config_returns_503_when_required_settings_are_missing() -> None:
    app = FastAPI()
    app.include_router(
        create_client_config_router(
            client_settings=ClientConfigSettings(
                firebase_api_key=None,
                firebase_messaging_sender_id=None,
                firebase_android_app_id="android-app-id",
                firebase_ios_app_id="ios-app-id",
                firebase_google_ios_client_id="google-ios-client-id",
                firebase_google_server_client_id="google-server-client-id",
            ),
            firebase_settings=FirebaseAuthSettings(
                firebase_project_id="voxos-project",
            ),
            voice_settings=VoiceAgentSettings(
                voice_agent_name="voxos-mobile-agent",
            ),
        ),
    )

    response = TestClient(app).get("/api/client-config")

    assert response.status_code == 503
    assert response.json() == {
        "detail": (
            "Client configuration is incomplete; missing required settings: "
            "FIREBASE_API_KEY, FIREBASE_MESSAGING_SENDER_ID"
        ),
    }


def test_client_config_omits_unset_optional_firebase_fields() -> None:
    app = FastAPI()
    app.include_router(
        create_client_config_router(
            client_settings=ClientConfigSettings(
                firebase_api_key=TEST_FIREBASE_API_KEY,
                firebase_messaging_sender_id="123456789",
                firebase_android_app_id="android-app-id",
                firebase_ios_app_id="ios-app-id",
                firebase_google_ios_client_id="google-ios-client-id",
                firebase_google_server_client_id="google-server-client-id",
                firebase_auth_domain=" ",
                firebase_storage_bucket=None,
            ),
            firebase_settings=FirebaseAuthSettings(
                firebase_project_id="voxos-project",
            ),
            voice_settings=VoiceAgentSettings(
                voice_agent_name="voxos-mobile-agent",
            ),
        ),
    )

    response = TestClient(app).get("/api/client-config")

    assert response.status_code == 200
    assert "auth_domain" not in response.json()["firebase"]
    assert "storage_bucket" not in response.json()["firebase"]
    assert "web_app_id" not in response.json()["firebase"]


def test_client_settings_load_public_firebase_values_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "FIREBASE_API_KEY": TEST_FIREBASE_API_KEY,
        "FIREBASE_MESSAGING_SENDER_ID": "123456789",
        "FIREBASE_ANDROID_APP_ID": "android-app-id",
        "FIREBASE_IOS_APP_ID": "ios-app-id",
        "FIREBASE_WEB_APP_ID": "web-app-id",
        "FIREBASE_GOOGLE_IOS_CLIENT_ID": "google-ios-client-id",
        "FIREBASE_GOOGLE_SERVER_CLIENT_ID": "google-server-client-id",
        "FIREBASE_AUTH_DOMAIN": "voxos.example.test",
        "FIREBASE_STORAGE_BUCKET": "voxos-storage.example.test",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = ClientConfigSettings()

    assert settings.firebase_api_key == values["FIREBASE_API_KEY"]
    assert settings.firebase_messaging_sender_id == values["FIREBASE_MESSAGING_SENDER_ID"]
    assert settings.firebase_android_app_id == values["FIREBASE_ANDROID_APP_ID"]
    assert settings.firebase_ios_app_id == values["FIREBASE_IOS_APP_ID"]
    assert settings.firebase_web_app_id == values["FIREBASE_WEB_APP_ID"]
    assert settings.firebase_google_ios_client_id == values["FIREBASE_GOOGLE_IOS_CLIENT_ID"]
    assert settings.firebase_google_server_client_id == values["FIREBASE_GOOGLE_SERVER_CLIENT_ID"]
    assert settings.firebase_auth_domain == values["FIREBASE_AUTH_DOMAIN"]
    assert settings.firebase_storage_bucket == values["FIREBASE_STORAGE_BUCKET"]


def test_client_config_trims_required_public_values() -> None:
    app = FastAPI()
    app.include_router(
        create_client_config_router(
            client_settings=ClientConfigSettings(
                firebase_api_key=f" {TEST_FIREBASE_API_KEY} ",
                firebase_messaging_sender_id=" 123456789 ",
                firebase_android_app_id=" android-app-id ",
                firebase_ios_app_id=" ios-app-id ",
                firebase_web_app_id=" web-app-id ",
                firebase_google_ios_client_id=" google-ios-client-id ",
                firebase_google_server_client_id=" google-server-client-id ",
                firebase_auth_domain=None,
                firebase_storage_bucket=None,
            ),
            firebase_settings=FirebaseAuthSettings(
                firebase_project_id=" voxos-project ",
            ),
            voice_settings=VoiceAgentSettings(
                voice_agent_name=" voxos-mobile-agent ",
            ),
        ),
    )

    response = TestClient(app).get("/api/client-config")

    assert response.status_code == 200
    assert response.json()["firebase"] == {
        "project_id": "voxos-project",
        "api_key": TEST_FIREBASE_API_KEY,
        "messaging_sender_id": "123456789",
        "android_app_id": "android-app-id",
        "ios_app_id": "ios-app-id",
        "web_app_id": "web-app-id",
        "google_ios_client_id": "google-ios-client-id",
        "google_server_client_id": "google-server-client-id",
    }
    assert response.json()["voice"]["agent_name"] == "voxos-mobile-agent"

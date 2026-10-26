from dataclasses import dataclass
from datetime import UTC, datetime

import jwt
import orjson
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlmodel import select
from starlette.requests import Request

from voxos.api.livekit import create_livekit_router
from voxos.auth.models import AuthenticatedUser
from voxos.credentials.records import UserSecretRecord
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.user_settings.models import UserLiveKitSettingsRecord
from voxos.voice.livekit_settings import UserLiveKitSettingsService
from voxos.voice.livekit_tokens import (
    LiveKitIdentityMetadataBuilder,
    LiveKitVoiceToken,
    LiveKitVoiceTokenRequest,
    LiveKitVoiceTokenService,
    ResolvedLiveKitCredentials,
)
from voxos.voice.route_signing import LiveKitRouteSigner


@dataclass(frozen=True, slots=True)
class StaticAuthDependency:
    authenticated_user: AuthenticatedUser

    async def __call__(self, request: Request) -> AuthenticatedUser:
        return self.authenticated_user


@dataclass(slots=True)
class ReadyCallRouter:
    token_template: LiveKitVoiceTokenService
    routed_user_ids: list[str]

    async def create_join_token(
        self,
        *,
        authenticated_user: AuthenticatedUser,
        credentials: ResolvedLiveKitCredentials,
        request: LiveKitVoiceTokenRequest,
        source: str,
    ) -> LiveKitVoiceToken:
        assert source == "livekit_api"
        self.routed_user_ids.append(authenticated_user.user_id)
        return LiveKitVoiceTokenService(
            api_key=credentials.api_key,
            api_secret=credentials.api_secret,
            metadata_builder=self.token_template.metadata_builder,
            ttl_seconds=self.token_template.ttl_seconds,
            route_signer=self.token_template.route_signer,
        ).create_join_token(
            authenticated_user=authenticated_user,
            request=request,
        )


def authenticated_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id="local-user-123",
        firebase_user_id="firebase-user-123",
        email="user@example.com",
        email_verified=True,
        display_name="Test User",
        photo_url="https://example.com/user.png",
    )


def token_service() -> LiveKitVoiceTokenService:
    return LiveKitVoiceTokenService(
        api_key=None,
        api_secret=None,
        metadata_builder=LiveKitIdentityMetadataBuilder(user_metadata_key="user_id"),
        ttl_seconds=900,
        route_signer=LiveKitRouteSigner(
            secret=SecretStr("route-signing-secret-at-least-32-characters"),
            ttl_seconds=900,
        ),
    )


def unconfigured_token_service() -> LiveKitVoiceTokenService:
    return LiveKitVoiceTokenService(
        api_key=None,
        api_secret=None,
        metadata_builder=LiveKitIdentityMetadataBuilder(user_metadata_key="user_id"),
        ttl_seconds=900,
    )


async def save_livekit_settings(
    *,
    session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    user_id: str = "local-user-123",
) -> None:
    async with session_factory() as session:
        await UserLiveKitSettingsService(
            session=session,
            secret_vault=user_secret_vault,
        ).set_settings(
            user_id=user_id,
            livekit_url="wss://saved-livekit.example.test",
            api_key=SecretStr("saved-livekit-key"),
            api_secret=SecretStr("0123456789abcdef0123456789abcdef"),
        )


@pytest.mark.asyncio
async def test_livekit_token_endpoint_returns_token_source_response(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await save_livekit_settings(
        session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
    )

    app = FastAPI()
    ready_call_router = ReadyCallRouter(
        token_template=token_service(),
        routed_user_ids=[],
    )
    app.include_router(
        create_livekit_router(
            auth_dependency=StaticAuthDependency(authenticated_user()),
            token_service=token_service(),
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            call_router=ready_call_router,
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/api/livekit/token",
        json={
            "room_name": "voice-room",
            "participant_name": "Browser User",
            "participant_metadata": orjson.dumps(
                {
                    "device": "browser",
                    "user_id": "client",
                    "voxos_location": {
                        "latitude": 42.4304,
                        "longitude": 19.2594,
                        "horizontal_accuracy_m": 25,
                        "captured_at": datetime.now(UTC).isoformat(),
                    },
                },
            ).decode(),
            "room_config": {
                "agents": [
                    {
                        "agent_name": "voxos-voice-agent",
                        "metadata": '{"user_id":"client"}',
                    },
                ],
            },
        },
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert response.status_code == 200
    body = response.json()
    claims = jwt.decode(body["participant_token"], options={"verify_signature": False})

    assert body["server_url"] == "wss://saved-livekit.example.test"
    assert body["agent_id"] is not None
    assert body["runtime_session_id"] is not None
    assert claims["sub"] == "local-user-123"
    assert claims["name"] == "Browser User"
    metadata = orjson.loads(claims["metadata"])
    assert metadata["agent_id"] == body["agent_id"]
    assert metadata["device"] == "browser"
    assert metadata["voxos_location"]["latitude"] == 42.4304
    assert metadata["runtime_session_id"] == body["runtime_session_id"]
    assert metadata["user_id"] == "local-user-123"
    assert isinstance(metadata["voxos_route"], str)
    assert claims["roomConfig"]["agents"][0]["agentName"] == "voxos-voice-agent"
    assert orjson.loads(claims["roomConfig"]["agents"][0]["metadata"]) == metadata
    assert ready_call_router.routed_user_ids == ["local-user-123"]


@pytest.mark.asyncio
async def test_livekit_token_endpoint_rejects_invalid_metadata(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await save_livekit_settings(
        session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
    )

    app = FastAPI()
    app.include_router(
        create_livekit_router(
            auth_dependency=StaticAuthDependency(authenticated_user()),
            token_service=token_service(),
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/api/livekit/token",
        json={
            "room_name": "voice-room",
            "participant_metadata": "not-json",
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_livekit_token_endpoint_rejects_unregistered_agent_name(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await save_livekit_settings(
        session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
    )
    app = FastAPI()
    app.include_router(
        create_livekit_router(
            auth_dependency=StaticAuthDependency(authenticated_user()),
            token_service=token_service(),
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/api/livekit/token",
        json={
            "room_name": "voice-room",
            "room_config": {
                "agents": [{"agent_name": "not-the-configured-worker"}],
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == ("agent_name does not match the configured voice worker")


def test_livekit_token_endpoint_requires_livekit_configuration() -> None:
    app = FastAPI()
    app.include_router(
        create_livekit_router(
            auth_dependency=StaticAuthDependency(authenticated_user()),
            token_service=token_service(),
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/api/livekit/token",
        json={"room_name": "voice-room"},
    )

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_livekit_token_endpoint_maps_undecryptable_saved_credentials_to_503(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await save_livekit_settings(
        session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
    )
    async with sqlite_session_factory() as session:
        result = await session.exec(select(UserSecretRecord))
        secret_record = result.one()
        secret_record.ciphertext = b"corrupted-ciphertext"
        await session.commit()

    app = FastAPI()
    app.include_router(
        create_livekit_router(
            auth_dependency=StaticAuthDependency(authenticated_user()),
            token_service=token_service(),
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
        ),
    )

    response = TestClient(app).post(
        "/api/livekit/token",
        json={"room_name": "voice-room"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "LiveKit is not configured"}


@pytest.mark.asyncio
async def test_livekit_token_endpoint_maps_invalid_legacy_url_to_503(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await save_livekit_settings(
        session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
    )
    async with sqlite_session_factory() as session:
        result = await session.exec(select(UserLiveKitSettingsRecord))
        settings_record = result.one()
        settings_record.livekit_url = "https://invalid-livekit.example.test/path"
        await session.commit()

    app = FastAPI()
    app.include_router(
        create_livekit_router(
            auth_dependency=StaticAuthDependency(authenticated_user()),
            token_service=token_service(),
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
        ),
    )

    response = TestClient(app).post(
        "/api/livekit/token",
        json={"room_name": "voice-room"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "LiveKit is not configured"}


@pytest.mark.asyncio
async def test_livekit_settings_endpoint_saves_credentials_write_only(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    app = FastAPI()
    app.include_router(
        create_livekit_router(
            auth_dependency=StaticAuthDependency(authenticated_user()),
            secret_vault=user_secret_vault,
            session_factory=sqlite_session_factory,
            token_service=unconfigured_token_service(),
        ),
    )
    client = TestClient(app)

    initial_response = client.get(
        "/api/livekit/settings",
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert initial_response.status_code == 200
    assert initial_response.json() == {
        "configured": False,
        "livekit_url": None,
    }

    response = client.put(
        "/api/livekit/settings",
        json={
            "livekit_url": "wss://saved-livekit.example.test",
            "livekit_api_key": "saved-livekit-key",
            "livekit_api_secret": "saved-livekit-secret",
        },
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "livekit_url": "wss://saved-livekit.example.test",
    }
    assert "saved-livekit-key" not in response.text
    assert "saved-livekit-secret" not in response.text
    assert "livekit_api_key" not in response.text
    assert "livekit_api_secret" not in response.text

    async with sqlite_session_factory() as session:
        settings_result = await session.exec(select(UserLiveKitSettingsRecord))
        settings_record = settings_result.one()
        secret_result = await session.exec(select(UserSecretRecord))
        secret_record = secret_result.one()

    assert settings_record.livekit_url == "wss://saved-livekit.example.test"
    assert b"saved-livekit-key" not in secret_record.ciphertext
    assert b"saved-livekit-secret" not in secret_record.ciphertext


@pytest.mark.asyncio
async def test_livekit_token_endpoint_uses_saved_credentials(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await save_livekit_settings(
        session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
    )

    app = FastAPI()
    app.include_router(
        create_livekit_router(
            auth_dependency=StaticAuthDependency(authenticated_user()),
            secret_vault=user_secret_vault,
            session_factory=sqlite_session_factory,
            token_service=unconfigured_token_service(),
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/api/livekit/token",
        json={
            "room_name": "voice-room",
            "participant_name": "Browser User",
        },
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert response.status_code == 200
    body = response.json()
    claims = jwt.decode(
        body["participant_token"],
        "0123456789abcdef0123456789abcdef",
        algorithms=["HS256"],
        options={"verify_aud": False},
    )
    assert body["server_url"] == "wss://saved-livekit.example.test"
    assert claims["sub"] == "local-user-123"

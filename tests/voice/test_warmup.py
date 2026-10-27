from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.requests import Request

from voxos.api.main import create_app
from voxos.auth.models import AuthenticatedUser
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory, voice_worker_session_factory
from voxos.voice.demand import (
    VoiceProjectKeyFactory,
    VoiceWorkerDemandStore,
    VoiceWorkerWarmRequest,
)
from voxos.voice.livekit_settings import UserLiveKitSettingsService
from voxos.voice.livekit_tokens import ResolvedLiveKitCredentials
from voxos.voice.settings import VoiceAgentSettings
from voxos.voice.warmup import (
    ActivityWarmingAuthDependency,
    VoiceWorkerActivityWarmer,
)


def warm_settings(*, enabled: bool = True) -> VoiceAgentSettings:
    return VoiceAgentSettings(
        _env_file=None,
        voice_route_signing_secret=SecretStr(
            "route-signing-secret-at-least-32-characters",
        ),
        voice_worker_idle_ttl_seconds=60,
        voice_warm_user_workers=enabled,
    )


async def save_livekit_settings(
    *,
    session_factory: AsyncSessionFactory,
    secret_vault: UserSecretVaultService,
    user_id: str,
) -> None:
    scoped_factory = tenant_scoped_session_factory(
        session_factory=session_factory,
        user_id=user_id,
    )
    async with scoped_factory() as session:
        await UserLiveKitSettingsService(
            session=session,
            secret_vault=secret_vault,
        ).set_settings(
            user_id=user_id,
            livekit_url="wss://activity.livekit.test",
            api_key=SecretStr("activity-key"),  # pragma: allowlist secret
            api_secret=SecretStr("activity-secret"),  # pragma: allowlist secret
        )


def test_voice_activity_warming_is_enabled_by_default() -> None:
    assert VoiceAgentSettings(_env_file=None).voice_warm_user_workers is True


def test_permanent_voice_warming_does_not_depend_on_idle_ttl(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    warmer = VoiceWorkerActivityWarmer(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        settings=warm_settings().model_copy(
            update={"voice_worker_idle_ttl_seconds": 0},
        ),
    )

    assert warmer.enabled is True


@pytest.mark.asyncio
async def test_authenticated_activity_persists_one_tenant_project_warm_request(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    user_id = "activity-user"
    settings = warm_settings()
    await save_livekit_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id=user_id,
    )
    warmer = VoiceWorkerActivityWarmer(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        settings=settings,
    )

    warmer.record_activity(user_id)
    warmer.record_activity(user_id)
    await warmer.wait_for_pending()

    demands = await VoiceWorkerDemandStore(
        session_factory=voice_worker_session_factory(sqlite_session_factory),
    ).list_live_demands()
    assert len(demands) == 1
    warm_request = demands[0]
    assert isinstance(warm_request, VoiceWorkerWarmRequest)
    assert warm_request.user_id == user_id
    assert settings.voice_route_signing_secret is not None
    assert warm_request.project_key == VoiceProjectKeyFactory(
        settings.voice_route_signing_secret,
    ).create(
        await resolved_livekit_settings(
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            user_id=user_id,
        ),
    )
    assert warm_request.expires_at is None

    ignored_demands = await VoiceWorkerDemandStore(
        session_factory=voice_worker_session_factory(sqlite_session_factory),
        warm_requests_enabled=False,
    ).list_live_demands()
    assert ignored_demands == ()
    retained_demands = await VoiceWorkerDemandStore(
        session_factory=voice_worker_session_factory(sqlite_session_factory),
    ).list_live_demands()
    assert retained_demands == (warm_request,)
    await warmer.aclose()


@pytest.mark.asyncio
async def test_disabled_voice_activity_warming_does_not_persist_request(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    warmer = VoiceWorkerActivityWarmer(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        settings=warm_settings(enabled=False),
    )

    warmer.record_activity("activity-user")
    await warmer.wait_for_pending()

    demands = await VoiceWorkerDemandStore(
        session_factory=voice_worker_session_factory(sqlite_session_factory),
    ).list_live_demands()
    assert demands == ()
    await warmer.aclose()


@dataclass(slots=True)
class RecordingWarmer:
    users: list[str] = field(default_factory=list)

    def record_activity(self, user_id: str) -> None:
        self.users.append(user_id)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_auth_wrapper_records_activity_after_successful_authentication() -> None:
    warmer = RecordingWarmer()

    async def authenticate(_: Request) -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id="authenticated-user",
            firebase_user_id="firebase-user",
        )

    dependency = ActivityWarmingAuthDependency(
        dependency=authenticate,
        warmer=warmer,
    )

    user = await dependency(Request({"type": "http"}))

    assert user.user_id == "authenticated-user"
    assert warmer.users == ["authenticated-user"]


def test_authenticated_application_request_records_activity(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    warmer = RecordingWarmer()

    async def authenticate(_: Request) -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id="authenticated-user",
            firebase_user_id="firebase-user",
        )

    app = create_app(
        auth_dependency=authenticate,
        secret_vault=user_secret_vault,
        session_factory=sqlite_session_factory,
        voice_settings=VoiceAgentSettings(_env_file=None),
        voice_worker_activity_warmer=warmer,
    )

    with TestClient(app) as client:
        response = client.get("/api/livekit/settings")

    assert response.status_code == 200
    assert warmer.users == ["authenticated-user"]


async def resolved_livekit_settings(
    *,
    session_factory: AsyncSessionFactory,
    secret_vault: UserSecretVaultService,
    user_id: str,
) -> ResolvedLiveKitCredentials:
    scoped_factory = tenant_scoped_session_factory(
        session_factory=session_factory,
        user_id=user_id,
    )
    async with scoped_factory() as session:
        return await UserLiveKitSettingsService(
            session=session,
            secret_vault=secret_vault,
        ).resolve_settings(user_id)

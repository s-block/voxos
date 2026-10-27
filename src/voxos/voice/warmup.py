import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from cachetools import TTLCache
from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError

from voxos.auth.models import AuthenticatedUser
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory
from voxos.voice.demand import (
    VoiceProjectKeyFactory,
    VoiceWorkerWarmRequestClient,
)
from voxos.voice.errors import (
    InvalidLiveKitCredentialsError,
    MissingLiveKitCredentialsError,
)
from voxos.voice.livekit_settings import UserLiveKitSettingsService
from voxos.voice.settings import VoiceAgentSettings

logger = logging.getLogger(__name__)
AuthenticatedUserDependency = Callable[[Request], Awaitable[AuthenticatedUser]]


class VoiceWorkerActivityWarmup(Protocol):
    def record_activity(self, user_id: str) -> None:
        """Request a tenant voice worker without delaying the API request."""

    async def aclose(self) -> None:
        """Stop outstanding warmup tasks."""


@dataclass(frozen=True, slots=True)
class ActivityWarmingAuthDependency:
    dependency: AuthenticatedUserDependency
    warmer: VoiceWorkerActivityWarmup

    async def __call__(self, request: Request) -> AuthenticatedUser:
        user = await self.dependency(request)
        self.warmer.record_activity(user.user_id)
        return user


@dataclass(slots=True)
class VoiceWorkerActivityWarmer:
    session_factory: AsyncSessionFactory
    secret_vault: UserSecretVaultService
    settings: VoiceAgentSettings
    _tasks: set[asyncio.Task[None]] = field(default_factory=set, init=False)
    _recent_users: TTLCache[str, bool] = field(init=False)
    _closed: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self._recent_users = TTLCache(
            maxsize=10_000,
            ttl=self._refresh_interval_seconds(),
        )

    @property
    def enabled(self) -> bool:
        return self.settings.voice_warm_user_workers and self.settings.voice_route_signing_secret is not None

    def record_activity(self, user_id: str) -> None:
        if not self.enabled or self._closed:
            return
        if user_id in self._recent_users:
            return
        self._recent_users[user_id] = True
        task = asyncio.create_task(
            self._request_warm_worker(user_id),
            name=f"voice-worker-activity-warmup-{user_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_for_pending(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks))

    async def aclose(self) -> None:
        self._closed = True
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        self._tasks.clear()

    async def _request_warm_worker(self, user_id: str) -> None:
        signing_secret = self.settings.voice_route_signing_secret
        if signing_secret is None:
            return
        scoped_factory = tenant_scoped_session_factory(
            session_factory=self.session_factory,
            user_id=user_id,
        )
        try:
            async with scoped_factory() as session:
                credentials = await UserLiveKitSettingsService(
                    session=session,
                    secret_vault=self.secret_vault,
                ).resolve_settings(user_id)
            await VoiceWorkerWarmRequestClient(
                session_factory=scoped_factory,
                user_id=user_id,
                project_key_factory=VoiceProjectKeyFactory(signing_secret),
            ).request(credentials=credentials)
        except (
            MissingLiveKitCredentialsError,
            InvalidLiveKitCredentialsError,
        ):
            logger.debug(
                "Skipped voice worker activity warmup without valid tenant settings",
                extra={"user_id": user_id},
            )
        except SQLAlchemyError:
            logger.exception(
                "Failed to persist voice worker activity warmup",
                extra={"user_id": user_id},
            )
        except Exception:
            logger.exception(
                "Voice worker activity warmup failed",
                extra={"user_id": user_id},
            )

    def _refresh_interval_seconds(self) -> float:
        return min(
            max(self.settings.voice_worker_idle_ttl_seconds / 3, 1.0),
            60.0,
        )

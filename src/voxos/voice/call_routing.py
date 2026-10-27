import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Protocol

import orjson
from livekit import api
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.auth.models import AuthenticatedUser
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory
from voxos.integrations.records import IntegrationCallSessionRecord
from voxos.voice.demand import (
    VoiceCallDemand,
    VoiceCallDemandClient,
    VoiceProjectKeyFactory,
)
from voxos.voice.endpoint_policy import livekit_log_target
from voxos.voice.errors import (
    MissingVoiceRouteMetadataError,
    MissingVoiceRouteSignerError,
    VoiceWorkerUnavailableError,
)
from voxos.voice.livekit_settings import UserLiveKitSettingsService
from voxos.voice.livekit_tokens import (
    LiveKitVoiceToken,
    LiveKitVoiceTokenRequest,
    LiveKitVoiceTokenService,
    ResolvedLiveKitCredentials,
)
from voxos.voice.settings import VoiceAgentSettings

logger = logging.getLogger(__name__)
OUTBOUND_CALL_ENDED_BEFORE_AGENT_READY_CODE = "outbound_call_ended_before_agent_ready"
VOICE_AGENT_READINESS_TIMEOUT_CODE = "voice_agent_readiness_timeout"


class LiveKitCallRouting(Protocol):
    def create_join_token(
        self,
        *,
        authenticated_user: AuthenticatedUser,
        credentials: ResolvedLiveKitCredentials,
        request: LiveKitVoiceTokenRequest,
        source: str,
    ) -> Awaitable[LiveKitVoiceToken]:
        """Wait for project capacity, then mint the tenant-routed join token."""


@dataclass(frozen=True, slots=True)
class LiveKitCallPreflight:
    settings: VoiceAgentSettings
    secret_vault: UserSecretVaultService

    async def __call__(self, *, session: AsyncSession, user_id: str) -> None:
        """Fail before provider call creation if tenant voice routing is unavailable."""
        if self.settings.voice_route_signing_secret is None:
            raise MissingVoiceRouteSignerError
        await UserLiveKitSettingsService(
            session=session,
            secret_vault=self.secret_vault,
        ).resolve_settings(user_id)


@dataclass(frozen=True, slots=True)
class LiveKitCallRouter:
    session_factory: AsyncSessionFactory
    settings: VoiceAgentSettings
    token_template: LiveKitVoiceTokenService

    async def request_worker(
        self,
        *,
        user_id: str,
        credentials: ResolvedLiveKitCredentials,
        agent_id: str,
        runtime_session_id: str,
        room_name: str,
        source: str,
    ) -> VoiceCallDemand:
        return await self._demand_client(user_id).request(
            credentials=credentials,
            agent_id=agent_id,
            runtime_session_id=runtime_session_id,
            room_name=room_name,
            source=source,
        )

    async def request_worker_in_session(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        credentials: ResolvedLiveKitCredentials,
        agent_id: str,
        runtime_session_id: str,
        room_name: str,
        source: str,
    ) -> VoiceCallDemand:
        """Persist call demand atomically with the caller's tenant transaction."""
        return await self._demand_client(user_id).request_in_session(
            session=session,
            credentials=credentials,
            agent_id=agent_id,
            runtime_session_id=runtime_session_id,
            room_name=room_name,
            source=source,
        )

    async def create_join_token(
        self,
        *,
        authenticated_user: AuthenticatedUser,
        credentials: ResolvedLiveKitCredentials,
        request: LiveKitVoiceTokenRequest,
        source: str,
    ) -> LiveKitVoiceToken:
        demand: VoiceCallDemand | None = None
        if request.agent_name is not None:
            if request.agent_name != self.settings.voice_agent_name:
                message = "agent_name does not match the configured voice worker"
                raise ValueError(message)
            if request.agent_id is None or request.runtime_session_id is None:
                raise MissingVoiceRouteMetadataError(
                    ("agent_id", "runtime_session_id"),
                )
            token_route_ttl = max(
                self.settings.voice_worker_demand_ttl_seconds,
                (self.token_template.ttl_seconds + self.settings.voice_route_clock_skew_seconds),
            )
            demand = await self._demand_client(
                authenticated_user.user_id,
                demand_ttl_seconds=token_route_ttl,
            ).request_and_wait(
                credentials=credentials,
                agent_id=request.agent_id,
                runtime_session_id=request.runtime_session_id,
                room_name=request.room_name,
                source=source,
            )

        token = LiveKitVoiceTokenService(
            api_key=credentials.api_key,
            api_secret=credentials.api_secret,
            metadata_builder=self.token_template.metadata_builder,
            ttl_seconds=self.token_template.ttl_seconds,
            route_signer=self.token_template.route_signer,
        ).create_join_token(
            authenticated_user=authenticated_user,
            request=request,
        )
        logger.info(
            "Prepared LiveKit call route",
            extra={
                "user_id": authenticated_user.user_id,
                "runtime_session_id": request.runtime_session_id,
                "source": source,
                "project_key": demand.project_key if demand is not None else None,
                "worker_id": demand.worker_id if demand is not None else None,
                "livekit_project": livekit_log_target(credentials.livekit_url),
            },
        )
        return token

    async def dispatch_agent(
        self,
        *,
        user_id: str,
        credentials: ResolvedLiveKitCredentials,
        agent_id: str,
        runtime_session_id: str,
        room_name: str,
    ) -> None:
        route_signer = self.token_template.route_signer
        if route_signer is None:
            raise MissingVoiceRouteSignerError
        route_token = route_signer.sign(
            user_id=user_id,
            agent_id=agent_id,
            runtime_session_id=runtime_session_id,
            room_name=room_name,
        )
        metadata = orjson.dumps(
            {self.settings.voice_route_metadata_key: route_token},
        ).decode()
        async with api.LiveKitAPI(
            url=credentials.livekit_url,
            api_key=credentials.api_key,
            api_secret=credentials.api_secret.get_secret_value(),
        ) as livekit_api:
            await livekit_api.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    agent_name=self.settings.voice_agent_name,
                    room=room_name,
                    metadata=metadata,
                ),
            )
        logger.info(
            "Dispatched outbound LiveKit voice agent",
            extra={
                "runtime_session_id": runtime_session_id,
                "livekit_project": livekit_log_target(credentials.livekit_url),
            },
        )

    def demand_client(self, user_id: str) -> VoiceCallDemandClient:
        return self._demand_client(user_id)

    def _demand_client(
        self,
        user_id: str,
        *,
        demand_ttl_seconds: int | None = None,
    ) -> VoiceCallDemandClient:
        if self.settings.voice_route_signing_secret is None:
            raise MissingVoiceRouteSignerError
        return VoiceCallDemandClient(
            session_factory=tenant_scoped_session_factory(
                session_factory=self.session_factory,
                user_id=user_id,
            ),
            user_id=user_id,
            project_key_factory=VoiceProjectKeyFactory(
                signing_secret=self.settings.voice_route_signing_secret,
            ),
            demand_ttl_seconds=(
                demand_ttl_seconds if demand_ttl_seconds is not None else self.settings.voice_worker_demand_ttl_seconds
            ),
            poll_interval_seconds=self.settings.voice_worker_poll_interval_seconds,
            start_timeout_seconds=self.settings.voice_worker_start_timeout_seconds,
            max_active_projects_per_tenant=(self.settings.voice_worker_max_active_projects_per_tenant),
            idle_project_ttl_seconds=self.settings.voice_worker_idle_ttl_seconds,
        )


@dataclass(frozen=True, slots=True)
class LiveKitCallDemandCoordinator:
    call_router: LiveKitCallRouter
    secret_vault: UserSecretVaultService

    async def __call__(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        agent_id: str,
        runtime_session_id: str,
        room_name: str,
        source: str,
    ) -> None:
        credentials = await UserLiveKitSettingsService(
            session=session,
            secret_vault=self.secret_vault,
        ).resolve_settings(user_id)
        await self.call_router.request_worker_in_session(
            session=session,
            user_id=user_id,
            credentials=credentials,
            agent_id=agent_id,
            runtime_session_id=runtime_session_id,
            room_name=room_name,
            source=source,
        )

    async def wait(self, *, user_id: str, runtime_session_id: str) -> None:
        await self.call_router.demand_client(user_id).wait_for_runtime(
            runtime_session_id,
        )

    async def dispatch(
        self,
        *,
        user_id: str,
        agent_id: str,
        runtime_session_id: str,
        room_name: str,
    ) -> None:
        async with tenant_scoped_session_factory(
            session_factory=self.call_router.session_factory,
            user_id=user_id,
        )() as session:
            credentials = await UserLiveKitSettingsService(
                session=session,
                secret_vault=self.secret_vault,
            ).resolve_settings(user_id)
        await self.call_router.dispatch_agent(
            user_id=user_id,
            credentials=credentials,
            agent_id=agent_id,
            runtime_session_id=runtime_session_id,
            room_name=room_name,
        )

    async def wait_for_agent_ready(
        self,
        *,
        user_id: str,
        runtime_session_id: str,
    ) -> None:
        session_factory = tenant_scoped_session_factory(
            session_factory=self.call_router.session_factory,
            user_id=user_id,
        )
        try:
            async with asyncio.timeout(
                self.call_router.settings.voice_agent_ready_timeout_seconds,
            ):
                while True:
                    async with session_factory() as session:
                        statement = select(IntegrationCallSessionRecord).where(
                            col(IntegrationCallSessionRecord.user_id) == user_id,
                            col(IntegrationCallSessionRecord.runtime_session_id) == runtime_session_id,
                        )
                        record = (await session.exec(statement)).one_or_none()
                    if record is None or record.ended_at is not None:
                        raise VoiceWorkerUnavailableError(
                            OUTBOUND_CALL_ENDED_BEFORE_AGENT_READY_CODE,
                        )
                    if record.agent_ready_at is not None:
                        return
                    await asyncio.sleep(
                        self.call_router.settings.voice_worker_poll_interval_seconds,
                    )
        except TimeoutError as exc:
            raise VoiceWorkerUnavailableError(
                VOICE_AGENT_READINESS_TIMEOUT_CODE,
            ) from exc

    async def cancel(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        runtime_session_id: str,
        failure_code: str,
    ) -> None:
        await self.call_router.demand_client(user_id).cancel_in_session(
            session=session,
            runtime_session_id=runtime_session_id,
            failure_code=failure_code,
        )

    async def complete(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        runtime_session_id: str,
    ) -> None:
        await self.call_router.demand_client(user_id).complete_in_session(
            session=session,
            runtime_session_id=runtime_session_id,
        )

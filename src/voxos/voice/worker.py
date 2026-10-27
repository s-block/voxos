import argparse
import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

import httpx
import orjson
from livekit import agents
from sqlalchemy.ext.asyncio import AsyncEngine

from voxos.agent_core.instructions import AGENT_INSTRUCTION_PROMPT_VERSION
from voxos.agent_core.mcp_tool_cache import McpToolCatalogCache
from voxos.agent_core.response_profiles import (
    CommunicationMedium,
    response_profile_prompt_snapshot,
)
from voxos.agent_identity.models import AgentScope
from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_memory.services import AgentMemoryService
from voxos.agent_planning.services import AgentPlanningService
from voxos.agent_runtime.effective_tool_policy import (
    EffectiveToolPolicyService,
    EffectiveToolPolicySnapshot,
)
from voxos.agent_runtime.history import TenantHistoryService
from voxos.agent_runtime.improvements import AgentImprovementService
from voxos.agent_runtime.learning_model import TextLearningModel
from voxos.agent_runtime.livekit_observer import (
    LiveKitObserverContext,
    LiveKitSessionObserver,
)
from voxos.agent_runtime.storage import AgentRuntimeStore, SqlModelAgentRuntimeStore
from voxos.agent_runtime.tool_authorization import SqlModelToolAuthorizationService
from voxos.agent_runtime.tool_routing import SqlModelToolRoutingService
from voxos.agent_skills.services import AgentSkillService
from voxos.browser_accounts.connection import McpBrowserCredentialResolver
from voxos.browser_accounts.model_resolution import BrowserTaskModelCredentialResolver
from voxos.browser_accounts.models import DEFAULT_BROWSER_COUNTRY_CODE
from voxos.browser_accounts.providers.browser_use_cloud import BrowserUseCloudProvider
from voxos.browser_accounts.providers.browserless import BrowserlessProvider
from voxos.browser_accounts.providers.composite import CompositeBrowserAccountProvider
from voxos.browser_accounts.providers.profileless import ProfilelessBrowserTaskExecutor
from voxos.browser_accounts.providers.stagehand_local import StagehandLocalProvider
from voxos.browser_accounts.secrets import BrowserAccountSecretStore
from voxos.browser_accounts.services import BrowserAccountService
from voxos.browser_accounts.settings import BrowserAccountSettings
from voxos.browser_runtime.service import BrowserRuntimeService
from voxos.credentials.services import CredentialsServiceFactory
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.engine import (
    create_database_engine,
    create_session_factory,
    create_session_factory_from_settings,
)
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.db.settings import DatabaseSettings
from voxos.db.tenant import (
    context_tenant_session_factory,
    tenant_scoped_session_factory,
    tenant_user_context,
)
from voxos.location.models import (
    ClientLocation,
    client_location_from_metadata,
    metadata_without_client_location,
)
from voxos.location.rendering import append_current_location_instructions
from voxos.mcp.settings import McpSettings
from voxos.observability.sentry import initialize_sentry
from voxos.persona.rendering import ToolPolicyContext
from voxos.persona.services import AgentPersonaService
from voxos.product_docs.retrieval import EnvironmentProductDocRetrievalService
from voxos.providers.base import (
    DEFAULT_REALTIME_SESSION_OPTIONS,
    RealtimeSpeechHandle,
    RealtimeVoiceSession,
    RealtimeVoiceSessionOptions,
)
from voxos.providers.factory import default_voice_provider_registry
from voxos.providers.registry import VoiceProviderRegistry
from voxos.security.encryption.factory import create_secret_encryption_service
from voxos.security.encryption.settings import EncryptionSettings
from voxos.security.outbound_endpoints import OutboundEndpointPolicy
from voxos.text.providers import OpenAICompatibleTextProvider
from voxos.utils.logging import configure_voxos_logging
from voxos.voice.browser_tools import (
    OWNER_BROWSER_ACCOUNT_VOICE_SOURCE,
    AuthenticatedBrowserVoiceToolProvider,
)
from voxos.voice.caller_access import (
    VoiceCallerAccess,
    VoiceCallerAccessLevel,
    VoiceCallerAccessResolver,
    policy_for_caller_access,
)
from voxos.voice.credentials import SqlModelVoiceCredentialResolver, VoiceCredentialResolver
from voxos.voice.demand import (
    VoiceCallDemand,
    VoiceCallDemandClient,
    VoiceCallDemandStatus,
    VoiceProjectKeyFactory,
)
from voxos.voice.errors import (
    InvalidVoiceConfigurationError,
    MissingVoiceRouteMetadataError,
    MissingVoiceRouteSignerError,
    VoiceProviderConnectionError,
    VoiceWorkerUnavailableError,
)
from voxos.voice.instructions import (
    CallerAwareVoiceInstructionProvider,
    PersonaVoiceInstructionProvider,
    ScopedVoiceInstructionProvider,
    VoiceInstructionProvider,
)
from voxos.voice.livekit_tokens import (
    DEFAULT_AGENT_METADATA_KEY,
    DEFAULT_ROUTE_METADATA_KEY,
    DEFAULT_RUNTIME_SESSION_METADATA_KEY,
)
from voxos.voice.mcp_tools import (
    INTERNAL_AGENT_MCP_TOOL_NAMES,
    CompositeVoiceToolProvider,
    InternalAgentVoiceToolProvider,
    UserMcpVoiceToolProvider,
)
from voxos.voice.models import UserVoicePreferences
from voxos.voice.outbound_calls import (
    OUTBOUND_CALL_KICKOFF_INSTRUCTIONS,
    OutboundCallContext,
    OutboundCallContextResolver,
    OutboundCallLifecycleService,
    OutboundOpeningGateResult,
    append_outbound_call_task,
)
from voxos.voice.route_signing import LiveKitRouteSigner
from voxos.voice.settings import VoiceAgentSettings
from voxos.voice.source_policy import (
    VoiceSessionKind,
    VoiceSessionSource,
    VoiceSessionSourcePolicy,
)
from voxos.voice.storage import SqlModelVoicePreferenceStore, VoicePreferenceStore
from voxos.voice.supervisor import (
    VoiceWorkerSupervisor,
    install_supervisor_signal_handlers,
    validate_voice_supervisor_settings,
)
from voxos.voice.telemetry import record_voice_timing
from voxos.voice.tools import ScopedVoiceToolProviderAdapter, VoiceToolProvider

VOICE_BROWSER_ACCOUNT_HTTP_CLIENT = httpx.AsyncClient()
VOICE_JOB_RUNTIME_KEY = "voxos.voice.runtime"

logger = logging.getLogger(__name__)
PROMPT_VERSION = AGENT_INSTRUCTION_PROMPT_VERSION


class VoiceJob(Protocol):
    metadata: str


class VoiceJobContext(Protocol):
    @property
    def job(self) -> VoiceJob:
        """LiveKit job metadata."""

    @property
    def room(self) -> object:
        """LiveKit room handle."""

    @property
    def room_metadata(self) -> str:
        """LiveKit room metadata."""

    @property
    def worker_id(self) -> str:
        """LiveKit registration that received the job."""

    async def connect(self) -> None:
        """Connect the worker to the LiveKit room."""

    def shutdown(self, reason: str = "") -> None:
        """Request shutdown of the current LiveKit job."""


class VoiceDemandLifecycleClient(Protocol):
    async def mark_active(
        self,
        runtime_session_id: str,
        worker_id: str,
        *,
        agent_id: str,
        room_name: str,
    ) -> VoiceCallDemand:
        """Mark and return the fenced routed demand."""

    async def heartbeat(self, runtime_session_id: str, worker_id: str) -> None:
        """Renew the active demand lease."""

    async def complete(self, runtime_session_id: str, worker_id: str) -> None:
        """Complete the routed demand."""


@dataclass(slots=True)
class VoiceCallDemandLifecycle:
    demand_client: VoiceDemandLifecycleClient
    user_id: str
    runtime_session_id: str
    agent_id: str
    room_name: str
    worker_id: str
    heartbeat_interval_seconds: float
    shutdown_job: Callable[[str], None]
    _heartbeat_task: asyncio.Task[None] | None = None
    _closed: bool = False
    _source: str | None = None

    async def start(self) -> VoiceCallDemand:
        demand = await self.demand_client.mark_active(
            self.runtime_session_id,
            self.worker_id,
            agent_id=self.agent_id,
            room_name=self.room_name,
        )
        self._source = demand.source
        logger.info(
            "Activated LiveKit call demand",
            extra={
                "user_id": self.user_id,
                "runtime_session_id": self.runtime_session_id,
                "source": demand.source,
                "session_kind": VoiceSessionSourcePolicy.classify(demand.source).value,
            },
        )
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat(),
            name=f"voice-demand-heartbeat-{self.runtime_session_id}",
        )
        return demand

    async def close(self, reason: str = "") -> None:
        if self._closed:
            return
        self._closed = True
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            await asyncio.gather(self._heartbeat_task, return_exceptions=True)
        try:
            await self.demand_client.complete(
                self.runtime_session_id,
                self.worker_id,
            )
            logger.info(
                "Completed LiveKit call demand",
                extra={
                    "user_id": self.user_id,
                    "runtime_session_id": self.runtime_session_id,
                    "source": self._source or "unknown",
                    "close_reason": reason or "unspecified",
                },
            )
        except Exception:
            logger.exception(
                "Failed to complete LiveKit call demand",
                extra={
                    "user_id": self.user_id,
                    "runtime_session_id": self.runtime_session_id,
                    "source": self._source or "unknown",
                    "close_reason": reason or "unspecified",
                },
            )

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval_seconds)
            try:
                await self.demand_client.heartbeat(
                    self.runtime_session_id,
                    self.worker_id,
                )
            except asyncio.CancelledError:
                raise
            except VoiceWorkerUnavailableError:
                logger.warning(
                    "Stopping LiveKit job after voice demand lease loss",
                    extra={
                        "user_id": self.user_id,
                        "runtime_session_id": self.runtime_session_id,
                        "source": self._source or "unknown",
                        "close_reason": "voice_demand_lease_lost",
                    },
                )
                self.shutdown_job("voice_demand_lease_lost")
                return
            except Exception:
                logger.exception(
                    "Failed to heartbeat LiveKit call demand",
                    extra={
                        "user_id": self.user_id,
                        "runtime_session_id": self.runtime_session_id,
                        "source": self._source or "unknown",
                    },
                )


@dataclass(frozen=True, slots=True)
class LiveKitVoiceJobContext:
    context: agents.JobContext

    @property
    def job(self) -> VoiceJob:
        return self.context.job

    @property
    def room(self) -> object:
        return self.context.room

    @property
    def room_metadata(self) -> str:
        return self.context.room.metadata

    @property
    def worker_id(self) -> str:
        return self.context.worker_id

    async def connect(self) -> None:
        await self.context.connect()

    def shutdown(self, reason: str = "") -> None:
        self.context.shutdown(reason)


@dataclass(frozen=True, slots=True)
class VoiceRouteIdentity:
    user_id: str
    agent_id: str
    runtime_session_id: str

    def scope(self, *, source: str = "voice.worker") -> AgentScope:
        return AgentScope(
            owner_user_id=self.user_id,
            agent_id=self.agent_id,
            session_id=self.runtime_session_id,
            channel="livekit",
            source=source,
        )


def validate_activated_demand(
    *,
    demand: VoiceCallDemand,
    route_identity: VoiceRouteIdentity,
    room_name: str | None,
    worker_id: str,
) -> None:
    if (
        demand.status != VoiceCallDemandStatus.ACTIVE
        or demand.user_id != route_identity.user_id
        or demand.agent_id != route_identity.agent_id
        or demand.runtime_session_id != route_identity.runtime_session_id
        or demand.room_name != room_name
        or demand.worker_id != worker_id
    ):
        reason_code = "demand_route_mismatch"
        raise VoiceWorkerUnavailableError(reason_code)


@dataclass(frozen=True, slots=True)
class VoiceAgentService:
    preference_store: VoicePreferenceStore
    credential_resolver: VoiceCredentialResolver
    provider_registry: VoiceProviderRegistry
    tool_provider: VoiceToolProvider
    user_metadata_key: str
    session_options: RealtimeVoiceSessionOptions = DEFAULT_REALTIME_SESSION_OPTIONS
    instruction_provider: VoiceInstructionProvider | None = None
    runtime_store: AgentRuntimeStore | None = None
    agent_identity_service: AgentIdentityService | None = None
    agent_metadata_key: str = DEFAULT_AGENT_METADATA_KEY
    runtime_session_metadata_key: str = DEFAULT_RUNTIME_SESSION_METADATA_KEY
    route_metadata_key: str = DEFAULT_ROUTE_METADATA_KEY
    route_signer: LiveKitRouteSigner | None = None
    caller_access_resolver: VoiceCallerAccessResolver | None = None
    effective_tool_policy_service: EffectiveToolPolicyService | None = None
    outbound_call_context_resolver: OutboundCallContextResolver | None = None
    outbound_call_lifecycle_service: OutboundCallLifecycleService | None = None
    outbound_answer_timeout_seconds: float = 45.0
    outbound_opening_timeout_seconds: float = 20.0
    outbound_opening_retry_attempts: int = 2

    async def start(
        self,
        context: VoiceJobContext,
        *,
        demand: VoiceCallDemand,
    ) -> None:
        route_identity = extract_route_identity_from_context(
            context=context,
            metadata_key=self.user_metadata_key,
            agent_metadata_key=self.agent_metadata_key,
            runtime_session_metadata_key=self.runtime_session_metadata_key,
            route_metadata_key=self.route_metadata_key,
            route_signer=self.route_signer,
        )
        validate_activated_demand(
            demand=demand,
            route_identity=route_identity,
            room_name=room_name_from_context(context),
            worker_id=context.worker_id,
        )
        with tenant_user_context(route_identity.user_id):
            await self._start_for_route(
                context=context,
                route_identity=route_identity,
                demand_source=demand.source,
            )

    async def _start_for_route(
        self,
        *,
        context: VoiceJobContext,
        route_identity: VoiceRouteIdentity,
        demand_source: str,
    ) -> None:
        startup_started = time.perf_counter()
        user_id = route_identity.user_id
        _, preferences, caller_access, outbound_context = await asyncio.gather(
            self._require_agent(route_identity),
            self.preference_store.get_preferences(user_id),
            self._resolve_caller_access(
                route_identity=route_identity,
                demand_source=demand_source,
            ),
            self._resolve_outbound_call_context(
                route_identity=route_identity,
                provider=demand_source,
            ),
        )
        configuration_loaded = time.perf_counter()
        agent_scope = route_identity.scope(
            source=(
                OWNER_BROWSER_ACCOUNT_VOICE_SOURCE
                if demand_source == VoiceSessionSource.LIVEKIT_API.value
                and caller_access.level == VoiceCallerAccessLevel.OWNER
                else "voice.worker"
            ),
        )
        credentials, effective_tool_policy = await asyncio.gather(
            self.credential_resolver.resolve(
                user_id=user_id,
                provider=preferences.provider,
                credential_ref=preferences.credential_ref,
            ),
            self._effective_tool_policy(
                agent_scope=agent_scope,
                caller_access=caller_access,
            ),
        )
        if credentials.provider != preferences.provider:
            raise InvalidVoiceConfigurationError.provider_mismatch()
        preferences = await self._preferences_with_rendered_instructions(
            agent_scope=agent_scope,
            preferences=preferences,
            caller_access=caller_access,
            effective_tool_policy=effective_tool_policy,
        )
        if outbound_context is not None:
            preferences = self._preferences_with_outbound_task(
                preferences,
                outbound_context,
            )
        current_location = (
            current_location_from_voice_context(context)
            if demand_source == VoiceSessionSource.LIVEKIT_API.value
            and caller_access.level == VoiceCallerAccessLevel.OWNER
            else None
        )
        session_configuration_ready = time.perf_counter()
        runtime_session_id: str | None = None
        session_observer: LiveKitSessionObserver | None = None
        if self.runtime_store is not None:
            prompt_snapshot = response_profile_prompt_snapshot(
                preferences.provider_config.agent_instructions,
                CommunicationMedium.VOICE,
            )
            runtime_session = await self.runtime_store.create_session(
                session_id=route_identity.runtime_session_id,
                user_id=user_id,
                agent_id=route_identity.agent_id,
                provider=preferences.provider.value,
                model=preferences.model,
                voice=preferences.voice,
                owner_authenticated=demand_source == VoiceSessionSource.LIVEKIT_API.value
                and caller_access.level == VoiceCallerAccessLevel.OWNER,
                room_name=room_name_from_context(context),
                room_metadata=metadata_without_client_location(
                    metadata_json_object(context.room_metadata),
                ),
                effective_tool_policy_json=effective_tool_policy.to_json(),
                prompt_version=PROMPT_VERSION,
                prompt_snapshot=prompt_snapshot,
                prompt_hash=prompt_hash(prompt_snapshot),
            )
            runtime_session_id = runtime_session.id
            session_observer = LiveKitSessionObserver(
                context=LiveKitObserverContext(
                    session_id=runtime_session.id,
                    user_id=user_id,
                    agent_id=route_identity.agent_id,
                ),
                runtime_store=self.runtime_store,
            )
        preferences = self._preferences_with_current_location(
            preferences,
            current_location,
        )
        runtime_persisted = time.perf_counter()

        logger.info(
            "Starting LiveKit voice session",
            extra={
                "user_id": user_id,
                "agent_id": route_identity.agent_id,
                "provider": preferences.provider.value,
                "model": preferences.model,
                "source": demand_source,
                "session_kind": VoiceSessionSourcePolicy.classify(demand_source).value,
                "location_attached": current_location is not None,
            },
        )

        try:
            scoped_tool_provider = ScopedVoiceToolProviderAdapter(
                provider=self.tool_provider,
                scope=agent_scope,
                effective_tool_policy=effective_tool_policy,
            )
            provider_session, _ = await asyncio.gather(
                self.provider_registry.create_session(
                    preferences=preferences,
                    credentials=credentials,
                    tool_provider=scoped_tool_provider,
                    session_options=self._session_options_for_source(demand_source),
                    session_observer=session_observer,
                ),
                context.connect(),
            )
            provider_session_created = time.perf_counter()
            await provider_session.start(
                context.room,
                participant_identity=(
                    outbound_context.media_participant_identity if outbound_context is not None else None
                ),
                audio_input_enabled=outbound_context is None,
            )
            provider_started = time.perf_counter()
            record_voice_timing(
                "VoxosVoiceSessionStartup",
                {
                    "provider": preferences.provider.value,
                    "model": preferences.model,
                    "source": demand_source,
                    "configuration_ms": (configuration_loaded - startup_started) * 1000,
                    "credentials_policy_instructions_ms": (session_configuration_ready - configuration_loaded) * 1000,
                    "runtime_persistence_ms": (runtime_persisted - session_configuration_ready) * 1000,
                    "tools_session_and_room_connect_ms": (provider_session_created - runtime_persisted) * 1000,
                    "provider_start_ms": (provider_started - provider_session_created) * 1000,
                    "total_ms": (provider_started - startup_started) * 1000,
                },
            )
            if outbound_context is None:
                provider_session.generate_initial_reply()
            else:
                await self._run_outbound_opening(
                    context=context,
                    provider_session=provider_session,
                    outbound_context=outbound_context,
                )
        except VoiceProviderConnectionError as exc:
            await self._mark_runtime_failed(
                session_id=runtime_session_id,
                user_id=user_id,
                agent_id=route_identity.agent_id,
                exc=exc,
            )
            raise
        except Exception as exc:
            await self._mark_runtime_failed(
                session_id=runtime_session_id,
                user_id=user_id,
                agent_id=route_identity.agent_id,
                exc=exc,
            )
            logger.exception(
                "LiveKit voice worker failed",
                extra={
                    "user_id": user_id,
                    "agent_id": route_identity.agent_id,
                    "provider": preferences.provider.value,
                    "model": preferences.model,
                    "source": demand_source,
                },
            )
            raise VoiceProviderConnectionError.worker_failed() from exc

    async def _require_agent(self, route_identity: VoiceRouteIdentity) -> None:
        if self.agent_identity_service is None:
            return
        await self.agent_identity_service.require_agent(
            owner_user_id=route_identity.user_id,
            agent_id=route_identity.agent_id,
        )

    async def _resolve_outbound_call_context(
        self,
        *,
        route_identity: VoiceRouteIdentity,
        provider: str,
    ) -> OutboundCallContext | None:
        if self.outbound_call_context_resolver is None:
            return None
        return await self.outbound_call_context_resolver.resolve(
            user_id=route_identity.user_id,
            agent_id=route_identity.agent_id,
            runtime_session_id=route_identity.runtime_session_id,
            provider=provider,
        )

    def _preferences_with_outbound_task(
        self,
        preferences: UserVoicePreferences,
        outbound_context: OutboundCallContext,
    ) -> UserVoicePreferences:
        rendered_config = preferences.provider_config.model_copy(
            update={
                "agent_instructions": append_outbound_call_task(
                    preferences.provider_config.agent_instructions,
                    outbound_context,
                ),
            },
        )
        return preferences.model_copy(update={"provider_config": rendered_config})

    @staticmethod
    def _preferences_with_current_location(
        preferences: UserVoicePreferences,
        current_location: ClientLocation | None,
    ) -> UserVoicePreferences:
        if current_location is None:
            return preferences
        rendered_config = preferences.provider_config.model_copy(
            update={
                "agent_instructions": append_current_location_instructions(
                    preferences.provider_config.agent_instructions,
                    current_location,
                ),
            },
        )
        return preferences.model_copy(update={"provider_config": rendered_config})

    async def _run_outbound_opening(
        self,
        *,
        context: VoiceJobContext,
        provider_session: RealtimeVoiceSession,
        outbound_context: OutboundCallContext,
    ) -> None:
        lifecycle = self.outbound_call_lifecycle_service
        if lifecycle is None:
            raise VoiceProviderConnectionError.worker_failed()
        await lifecycle.mark_agent_ready(outbound_context)
        gate_result = await lifecycle.wait_for_opening_gate(
            outbound_context,
            timeout_seconds=self.outbound_answer_timeout_seconds,
            max_attempts=self.outbound_opening_retry_attempts,
        )
        if gate_result != OutboundOpeningGateResult.READY:
            context.shutdown(f"outbound_opening_{gate_result.value}")
            return
        try:
            async with asyncio.timeout(self.outbound_opening_timeout_seconds):
                await provider_session.wait_for_media_ready()
        except TimeoutError:
            context.shutdown("outbound_media_ready_timeout")
            return

        for _attempt in range(self.outbound_opening_retry_attempts):
            claimed = await lifecycle.claim_opening(
                outbound_context,
                max_attempts=self.outbound_opening_retry_attempts,
            )
            if not claimed:
                context.shutdown("outbound_opening_not_claimed")
                return
            try:
                speech_handle = provider_session.generate_initial_reply(
                    instructions=OUTBOUND_CALL_KICKOFF_INSTRUCTIONS,
                )
                if speech_handle is None:
                    await lifecycle.mark_opening_failed(outbound_context)
                    continue
                await lifecycle.mark_opening_scheduled(outbound_context)
                provider_session.set_audio_input_enabled(True)
                await self._wait_for_outbound_opening(
                    lifecycle=lifecycle,
                    provider_session=provider_session,
                    speech_handle=speech_handle,
                    outbound_context=outbound_context,
                )
            except TimeoutError:
                await lifecycle.mark_opening_failed(outbound_context)
                continue
            except Exception:
                await lifecycle.mark_opening_failed(outbound_context)
                logger.exception(
                    "Outbound call opening failed",
                    extra={
                        "call_session_id": outbound_context.call_session_id,
                        "runtime_session_id": outbound_context.runtime_session_id,
                        "provider": outbound_context.provider,
                    },
                )
                continue
            if speech_handle.interrupted:
                await lifecycle.mark_opening_interrupted(outbound_context)
            else:
                await lifecycle.mark_opening_completed(outbound_context)
            return

        provider_session.set_audio_input_enabled(True)
        context.shutdown("outbound_opening_failed")

    async def _wait_for_outbound_opening(
        self,
        *,
        lifecycle: OutboundCallLifecycleService,
        provider_session: RealtimeVoiceSession,
        speech_handle: RealtimeSpeechHandle,
        outbound_context: OutboundCallContext,
    ) -> None:
        async def record_first_speech() -> None:
            await provider_session.wait_for_first_speech()
            await lifecycle.mark_first_agent_speech(outbound_context)

        async with asyncio.timeout(self.outbound_opening_timeout_seconds):
            await asyncio.gather(
                record_first_speech(),
                speech_handle.wait_for_playout(),
            )

    def _session_options_for_source(
        self,
        source: str,
    ) -> RealtimeVoiceSessionOptions:
        if VoiceSessionSourcePolicy.classify(source) != VoiceSessionKind.PROVIDER_BRIDGED_PHONE:
            return self.session_options
        return RealtimeVoiceSessionOptions(
            user_away_timeout_seconds=None,
            delete_room_on_close=self.session_options.delete_room_on_close,
            telephony=True,
        )

    async def _preferences_with_rendered_instructions(
        self,
        agent_scope: AgentScope,
        preferences: UserVoicePreferences,
        caller_access: VoiceCallerAccess,
        effective_tool_policy: EffectiveToolPolicySnapshot,
    ) -> UserVoicePreferences:
        if self.instruction_provider is None:
            return preferences

        if isinstance(self.instruction_provider, CallerAwareVoiceInstructionProvider):
            rendered_instructions = await self.instruction_provider.render_instructions_for_session(
                scope=agent_scope,
                preferences=preferences,
                caller_access=caller_access,
                effective_tool_policy=effective_tool_policy,
            )
        elif isinstance(self.instruction_provider, ScopedVoiceInstructionProvider):
            rendered_instructions = await self.instruction_provider.render_instructions_for_scope(
                scope=agent_scope,
                preferences=preferences,
            )
        else:
            rendered_instructions = await self.instruction_provider.render_instructions(
                user_id=agent_scope.owner_user_id,
                preferences=preferences,
            )
        rendered_config = preferences.provider_config.model_copy(
            update={"agent_instructions": rendered_instructions},
        )
        return preferences.model_copy(update={"provider_config": rendered_config})

    async def _resolve_caller_access(
        self,
        *,
        route_identity: VoiceRouteIdentity,
        demand_source: str,
    ) -> VoiceCallerAccess:
        if self.caller_access_resolver is not None:
            return await self.caller_access_resolver.resolve(
                user_id=route_identity.user_id,
                agent_id=route_identity.agent_id,
                runtime_session_id=route_identity.runtime_session_id,
                source=demand_source,
            )
        if demand_source == VoiceSessionSource.LIVEKIT_API.value:
            return VoiceCallerAccess(level=VoiceCallerAccessLevel.OWNER)
        return VoiceCallerAccess(level=VoiceCallerAccessLevel.LIMITED_CONTACT)

    async def _effective_tool_policy(
        self,
        *,
        agent_scope: AgentScope,
        caller_access: VoiceCallerAccess,
    ) -> EffectiveToolPolicySnapshot:
        if self.effective_tool_policy_service is None:
            base_policy = EffectiveToolPolicySnapshot(
                user_id=agent_scope.owner_user_id,
                agent_id=agent_scope.agent_id,
            )
        else:
            base_policy = await self.effective_tool_policy_service.snapshot_for_agent(
                user_id=agent_scope.owner_user_id,
                agent_id=agent_scope.agent_id,
                session_id=agent_scope.session_id,
                channel=agent_scope.channel,
                source="voice.worker",
            )
        return policy_for_caller_access(
            base_policy=base_policy,
            caller_access=caller_access,
        )

    async def _mark_runtime_failed(
        self,
        *,
        session_id: str | None,
        user_id: str,
        agent_id: str,
        exc: Exception,
    ) -> None:
        if self.runtime_store is None or session_id is None:
            return
        await self.runtime_store.fail_session(
            session_id=session_id,
            user_id=user_id,
            agent_id=agent_id,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )


def extract_route_identity_from_context(
    context: VoiceJobContext,
    metadata_key: str,
    agent_metadata_key: str,
    runtime_session_metadata_key: str,
    route_metadata_key: str = DEFAULT_ROUTE_METADATA_KEY,
    route_signer: LiveKitRouteSigner | None = None,
) -> VoiceRouteIdentity:
    if route_signer is None:
        raise MissingVoiceRouteSignerError

    room_name = room_name_from_context(context)
    if room_name is None:
        raise MissingVoiceRouteMetadataError(("room_name",))

    for metadata in (context.job.metadata, context.room_metadata):
        route_identity = extract_signed_route_identity_from_metadata(
            metadata=metadata,
            route_metadata_key=route_metadata_key,
            route_signer=route_signer,
            room_name=room_name,
        )
        if route_identity is not None:
            return route_identity

    raise MissingVoiceRouteMetadataError((route_metadata_key,))


def current_location_from_voice_context(
    context: VoiceJobContext,
) -> ClientLocation | None:
    now = utc_now()
    for metadata in (context.job.metadata, context.room_metadata):
        location = client_location_from_metadata(
            metadata_json_object(metadata),
            now=now,
        )
        if location is not None:
            return location
    return None


def extract_signed_route_identity_from_metadata(
    *,
    metadata: str,
    route_metadata_key: str,
    route_signer: LiveKitRouteSigner,
    room_name: str | None = None,
) -> VoiceRouteIdentity | None:
    payload = metadata_json_object(metadata)
    if not payload:
        return None

    route_token = _stripped_payload_text(payload, route_metadata_key)
    if route_token is None:
        return None

    signed_route = route_signer.verify(route_token, room_name=room_name)
    return VoiceRouteIdentity(
        user_id=signed_route.user_id,
        agent_id=signed_route.agent_id,
        runtime_session_id=signed_route.runtime_session_id,
    )


def _stripped_payload_text(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str):
        return None
    stripped_value = value.strip()
    return stripped_value or None


def metadata_json_object(metadata: str) -> dict[str, object]:
    if not metadata:
        return {}
    try:
        payload: object = orjson.loads(metadata)
    except orjson.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): value for key, value in payload.items()}


def room_name_from_context(context: VoiceJobContext) -> str | None:
    value = getattr(context.room, "name", None)
    if not isinstance(value, str):
        return None
    stripped_value = value.strip()
    return stripped_value or None


def prompt_hash(prompt: str) -> str:
    return sha256(prompt.encode("utf-8")).hexdigest()


def validate_voice_worker_settings(settings: VoiceAgentSettings) -> None:
    validate_voice_supervisor_settings(settings)


def build_voice_agent_service(
    settings: VoiceAgentSettings,
    database_settings: DatabaseSettings | None = None,
    encryption_settings: EncryptionSettings | None = None,
    secret_vault: UserSecretVaultService | None = None,
    tool_provider: VoiceToolProvider | None = None,
    instruction_provider: VoiceInstructionProvider | None = None,
    browser_account_settings: BrowserAccountSettings | None = None,
    session_factory: AsyncSessionFactory | None = None,
    mcp_tool_catalog_cache: McpToolCatalogCache | None = None,
) -> VoiceAgentService:
    resolved_session_factory = session_factory or create_session_factory_from_settings(
        database_settings or DatabaseSettings()
    )
    if secret_vault is None:
        secret_vault = CredentialsServiceFactory(
            create_secret_encryption_service(encryption_settings or EncryptionSettings()),
        ).user_secret_vault()
    if settings.voice_route_signing_secret is None:
        raise MissingVoiceRouteSignerError
    tenant_session_factory = context_tenant_session_factory(resolved_session_factory)
    runtime_store = SqlModelAgentRuntimeStore(session_factory=tenant_session_factory)
    agent_identity_service = AgentIdentityService(session_factory=tenant_session_factory)
    tool_authorization_service = SqlModelToolAuthorizationService(
        session_factory=tenant_session_factory,
    )
    effective_tool_policy_service = EffectiveToolPolicyService(
        authorization_service=tool_authorization_service,
        routing_service=SqlModelToolRoutingService(
            session_factory=tenant_session_factory,
        ),
    )
    memory_service = AgentMemoryService(session_factory=tenant_session_factory)
    skill_service = AgentSkillService(session_factory=tenant_session_factory)
    planning_service = AgentPlanningService(session_factory=tenant_session_factory)
    resolved_browser_account_settings = browser_account_settings or BrowserAccountSettings()
    browser_provider = CompositeBrowserAccountProvider(
        browser_use=BrowserUseCloudProvider(
            http_client=VOICE_BROWSER_ACCOUNT_HTTP_CLIENT,
            settings=resolved_browser_account_settings,
        ),
        browserless=BrowserlessProvider(resolved_browser_account_settings),
        stagehand_local=StagehandLocalProvider(
            http_client=VOICE_BROWSER_ACCOUNT_HTTP_CLIENT,
            settings=resolved_browser_account_settings,
        ),
    )
    browser_model_resolver = BrowserTaskModelCredentialResolver(
        session_factory=tenant_session_factory,
        secret_vault=secret_vault,
    )
    browser_account_service = BrowserAccountService(
        session_factory=tenant_session_factory,
        provider=browser_provider,
        credential_resolver=McpBrowserCredentialResolver(secret_vault),
        secret_store=BrowserAccountSecretStore(secret_vault),
        settings=resolved_browser_account_settings,
        model_credential_resolver=browser_model_resolver,
    )
    browser_runtime_service = BrowserRuntimeService(
        executor=ProfilelessBrowserTaskExecutor(
            provider=browser_provider,
            settings=resolved_browser_account_settings,
        ),
        model_credential_resolver=browser_model_resolver,
        max_task_steps=resolved_browser_account_settings.max_task_steps,
        browser_country_code=DEFAULT_BROWSER_COUNTRY_CODE,
    )
    return VoiceAgentService(
        preference_store=SqlModelVoicePreferenceStore(
            session_factory=tenant_session_factory,
        ),
        credential_resolver=SqlModelVoiceCredentialResolver(
            session_factory=tenant_session_factory,
            secret_vault=secret_vault,
        ),
        provider_registry=default_voice_provider_registry(),
        tool_provider=tool_provider
        or CompositeVoiceToolProvider(
            providers=(
                InternalAgentVoiceToolProvider(
                    memory_service=memory_service,
                    skill_service=skill_service,
                    planning_service=planning_service,
                    history_service=TenantHistoryService(tenant_session_factory, tool_authorization_service),
                    improvement_service=AgentImprovementService(
                        tenant_session_factory,
                        TenantHistoryService(tenant_session_factory, tool_authorization_service),
                        memory_service,
                        skill_service,
                        TextLearningModel(
                            tenant_session_factory,
                            secret_vault,
                            OpenAICompatibleTextProvider(
                                outbound_endpoint_policy=OutboundEndpointPolicy.from_settings(),
                            ),
                        ),
                    ),
                    contact_session_factory=tenant_session_factory,
                    product_docs_service=EnvironmentProductDocRetrievalService(
                        session_factory=tenant_session_factory,
                    ),
                    authorization_service=tool_authorization_service,
                ),
                UserMcpVoiceToolProvider(
                    session_factory=tenant_session_factory,
                    secret_vault=secret_vault,
                    authorization_service=tool_authorization_service,
                    tool_catalog_cache=mcp_tool_catalog_cache,
                ),
                AuthenticatedBrowserVoiceToolProvider(
                    profile_service=browser_account_service,
                    runtime_service=browser_runtime_service,
                    authorization_service=tool_authorization_service,
                ),
            ),
        ),
        user_metadata_key=settings.voice_user_metadata_key,
        route_metadata_key=settings.voice_route_metadata_key,
        route_signer=LiveKitRouteSigner(
            secret=settings.voice_route_signing_secret,
            ttl_seconds=settings.voice_token_ttl_seconds,
            clock_skew_seconds=settings.voice_route_clock_skew_seconds,
        ),
        session_options=RealtimeVoiceSessionOptions(
            user_away_timeout_seconds=settings.voice_inactivity_timeout_seconds,
            delete_room_on_close=settings.voice_delete_room_on_close,
        ),
        instruction_provider=instruction_provider
        or PersonaVoiceInstructionProvider(
            persona_service=AgentPersonaService(),
            identity_service=agent_identity_service,
            memory_service=memory_service,
            skill_service=skill_service,
            planning_service=planning_service,
            tool_policy_context=ToolPolicyContext(
                available_tools=INTERNAL_AGENT_MCP_TOOL_NAMES,
                confirmation_required_actions=(
                    "create_agent_memory",
                    "archive_agent_memory",
                    "create_user_skill",
                    "update_user_skill",
                    "archive_user_skill",
                    "complete_task_plan",
                ),
            ),
            tool_authorization_service=tool_authorization_service,
        ),
        runtime_store=runtime_store,
        agent_identity_service=agent_identity_service,
        caller_access_resolver=VoiceCallerAccessResolver(
            session_factory=tenant_session_factory,
        ),
        effective_tool_policy_service=effective_tool_policy_service,
        outbound_call_context_resolver=OutboundCallContextResolver(
            session_factory=tenant_session_factory,
        ),
        outbound_call_lifecycle_service=OutboundCallLifecycleService(
            session_factory=tenant_session_factory,
            poll_interval_seconds=settings.voice_worker_poll_interval_seconds,
        ),
        outbound_answer_timeout_seconds=settings.voice_outbound_answer_timeout_seconds,
        outbound_opening_timeout_seconds=settings.voice_outbound_opening_timeout_seconds,
        outbound_opening_retry_attempts=settings.voice_outbound_opening_retry_attempts,
    )


@dataclass(slots=True)
class VoiceJobRuntime:
    settings: VoiceAgentSettings
    database_engine: AsyncEngine
    session_factory: AsyncSessionFactory
    service: VoiceAgentService
    closed: bool = False

    async def close(self, _reason: str = "") -> None:
        if self.closed:
            return
        self.closed = True
        await self.database_engine.dispose()


def create_voice_job_runtime(
    settings: VoiceAgentSettings | None = None,
) -> VoiceJobRuntime:
    resolved_settings = settings or VoiceAgentSettings()
    database_engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(database_engine)
    secret_vault = CredentialsServiceFactory(
        create_secret_encryption_service(EncryptionSettings()),
    ).user_secret_vault()
    mcp_settings = McpSettings()
    service = build_voice_agent_service(
        resolved_settings,
        secret_vault=secret_vault,
        session_factory=session_factory,
        mcp_tool_catalog_cache=McpToolCatalogCache(
            max_entries=mcp_settings.mcp_tool_catalog_cache_max_entries,
            ttl_seconds=mcp_settings.mcp_tool_catalog_cache_ttl_seconds,
        ),
    )
    return VoiceJobRuntime(
        settings=resolved_settings,
        database_engine=database_engine,
        session_factory=session_factory,
        service=service,
    )


def prewarm_voice_job_process(process: agents.JobProcess) -> None:
    """Build process-local, tenant-neutral voice services before a job arrives."""

    initialize_sentry("voice-worker")
    configure_voxos_logging()
    process.userdata[VOICE_JOB_RUNTIME_KEY] = create_voice_job_runtime()


async def entrypoint(context: agents.JobContext) -> None:
    initialize_sentry("voice-worker")
    configure_voxos_logging()
    runtime_value = context.proc.userdata.get(VOICE_JOB_RUNTIME_KEY)
    runtime = runtime_value if isinstance(runtime_value, VoiceJobRuntime) else create_voice_job_runtime()
    context.add_shutdown_callback(runtime.close)
    settings = runtime.settings
    wrapped_context = LiveKitVoiceJobContext(context)
    if settings.voice_route_signing_secret is None:
        raise MissingVoiceRouteSignerError
    route_identity = extract_route_identity_from_context(
        context=wrapped_context,
        metadata_key=settings.voice_user_metadata_key,
        agent_metadata_key=DEFAULT_AGENT_METADATA_KEY,
        runtime_session_metadata_key=DEFAULT_RUNTIME_SESSION_METADATA_KEY,
        route_metadata_key=settings.voice_route_metadata_key,
        route_signer=LiveKitRouteSigner(
            secret=settings.voice_route_signing_secret,
            ttl_seconds=settings.voice_token_ttl_seconds,
            clock_skew_seconds=settings.voice_route_clock_skew_seconds,
        ),
    )
    room_name = room_name_from_context(wrapped_context)
    if room_name is None:
        raise MissingVoiceRouteMetadataError(("room_name",))
    demand_client = VoiceCallDemandClient(
        session_factory=tenant_scoped_session_factory(
            session_factory=runtime.session_factory,
            user_id=route_identity.user_id,
        ),
        user_id=route_identity.user_id,
        project_key_factory=VoiceProjectKeyFactory(
            signing_secret=settings.voice_route_signing_secret,
        ),
        demand_ttl_seconds=settings.voice_worker_demand_ttl_seconds,
        poll_interval_seconds=settings.voice_worker_poll_interval_seconds,
        start_timeout_seconds=settings.voice_worker_start_timeout_seconds,
        max_active_projects_per_tenant=(settings.voice_worker_max_active_projects_per_tenant),
        idle_project_ttl_seconds=settings.voice_worker_idle_ttl_seconds,
    )
    lifecycle = VoiceCallDemandLifecycle(
        demand_client=demand_client,
        user_id=route_identity.user_id,
        runtime_session_id=route_identity.runtime_session_id,
        agent_id=route_identity.agent_id,
        room_name=room_name,
        worker_id=wrapped_context.worker_id,
        shutdown_job=wrapped_context.shutdown,
        heartbeat_interval_seconds=max(
            settings.voice_worker_demand_ttl_seconds / 3,
            1.0,
        ),
    )
    demand = await lifecycle.start()
    context.add_shutdown_callback(lifecycle.close)
    try:
        await runtime.service.start(wrapped_context, demand=demand)
    except BaseException:
        await lifecycle.close("startup_failed")
        raise


def run_worker(
    settings: VoiceAgentSettings | None = None,
    *,
    until_idle: bool = False,
) -> None:
    initialize_sentry("voice-worker")
    configure_voxos_logging()
    resolved_settings = settings or VoiceAgentSettings()
    asyncio.run(
        run_database_backed_worker_from_settings(
            resolved_settings,
            until_idle=until_idle,
        ),
    )


async def run_database_backed_worker_from_settings(
    settings: VoiceAgentSettings | None = None,
    *,
    until_idle: bool = False,
) -> None:
    initialize_sentry("voice-worker")
    resolved_settings = settings or VoiceAgentSettings()
    validate_voice_worker_settings(resolved_settings)
    database_engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(database_engine)
    secret_vault = CredentialsServiceFactory(
        create_secret_encryption_service(EncryptionSettings()),
    ).user_secret_vault()
    supervisor = VoiceWorkerSupervisor(
        settings=resolved_settings,
        session_factory=session_factory,
        secret_vault=secret_vault,
        entrypoint=entrypoint,
        prewarm_fnc=prewarm_voice_job_process,
    )
    install_supervisor_signal_handlers(supervisor)
    try:
        if until_idle:
            await supervisor.run_until_idle()
        else:
            await supervisor.run_forever()
    finally:
        await database_engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the LiveKit voice supervisor")
    parser.add_argument(
        "--until-idle",
        action="store_true",
        help="Serve current voice demand and exit after the idle retention window",
    )
    arguments = parser.parse_args()
    run_worker(until_idle=arguments.until_idle)

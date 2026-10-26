from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

import orjson
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    TypeAdapter,
    field_validator,
)
from sqlalchemy.exc import SQLAlchemyError

from voxos.agent_identity.services import AgentIdentityNotFoundError
from voxos.agent_runtime.routing import AgentRouteContext, AgentRouteResolver
from voxos.auth.models import AuthenticatedUser
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory
from voxos.location.models import (
    CLIENT_LOCATION_METADATA_KEY,
    client_location_from_metadata,
    metadata_without_client_location,
)
from voxos.voice.call_routing import LiveKitCallRouter, LiveKitCallRouting
from voxos.voice.endpoint_policy import normalize_livekit_url
from voxos.voice.errors import (
    InvalidLiveKitCredentialsError,
    MissingLiveKitCredentialsError,
    MissingVoiceRouteSignerError,
    VoiceWorkerRegistrationError,
    VoiceWorkerUnavailableError,
)
from voxos.voice.livekit_settings import UserLiveKitSettingsService
from voxos.voice.livekit_tokens import (
    LiveKitVoiceTokenRequest,
    LiveKitVoiceTokenService,
    ResolvedLiveKitCredentials,
)
from voxos.voice.settings import VoiceAgentSettings

AuthenticatedUserDependency = Callable[[Request], Awaitable[AuthenticatedUser]]
JSON_METADATA_ADAPTER: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(
    dict[str, JsonValue],
)


class InvalidParticipantMetadataJsonError(ValueError):
    def __init__(self) -> None:
        super().__init__("participant_metadata must be valid JSON")


class InvalidParticipantMetadataTypeError(ValueError):
    def __init__(self) -> None:
        super().__init__("participant_metadata must be a JSON object")


class LiveKitTokenSourceAgentRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    agent_name: str | None = Field(default=None, min_length=1)
    metadata: str | None = Field(default=None, min_length=1)

    @field_validator("agent_name", "metadata", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class LiveKitTokenSourceRoomConfigRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    agents: tuple[LiveKitTokenSourceAgentRequest, ...] = Field(default=())


class LiveKitTokenSourceRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    room_name: str = Field(min_length=1, max_length=512)
    agent_id: str | None = Field(default=None, min_length=1, max_length=36)
    participant_name: str | None = Field(default=None, min_length=1)
    participant_metadata: str | None = Field(default=None, min_length=1)
    room_config: LiveKitTokenSourceRoomConfigRequest | None = None

    @field_validator(
        "room_name",
        "agent_id",
        "participant_name",
        "participant_metadata",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    def to_voice_token_request(
        self,
        *,
        agent_id: str | None,
        runtime_session_id: str | None,
    ) -> LiveKitVoiceTokenRequest:
        return LiveKitVoiceTokenRequest(
            room_name=self.room_name,
            participant_name=self.participant_name,
            client_metadata=self.client_metadata(),
            agent_name=self.agent_name(),
            agent_id=agent_id,
            runtime_session_id=runtime_session_id,
        )

    def client_metadata(self) -> dict[str, JsonValue]:
        if self.participant_metadata is None:
            return {}

        try:
            decoded = orjson.loads(self.participant_metadata)
        except orjson.JSONDecodeError as exc:
            raise InvalidParticipantMetadataJsonError from exc

        if not isinstance(decoded, dict):
            raise InvalidParticipantMetadataTypeError

        metadata = JSON_METADATA_ADAPTER.validate_python(decoded)
        location = client_location_from_metadata(metadata, now=utc_now())
        sanitized = metadata_without_client_location(metadata)
        if location is not None:
            sanitized[CLIENT_LOCATION_METADATA_KEY] = location.model_dump(
                mode="json",
            )
        return JSON_METADATA_ADAPTER.validate_python(sanitized)

    def agent_name(self) -> str | None:
        if self.room_config is None:
            return None

        for agent in self.room_config.agents:
            if agent.agent_name is not None:
                return agent.agent_name
        return None


class LiveKitTokenSourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    server_url: str = Field(min_length=1)
    participant_token: str = Field(min_length=1)
    agent_id: str | None = Field(default=None, min_length=1, max_length=36)
    runtime_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class LiveKitSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    livekit_url: str = Field(min_length=1, max_length=2048)
    livekit_api_key: SecretStr = Field(min_length=1)
    livekit_api_secret: SecretStr = Field(min_length=1)

    @field_validator("livekit_url", mode="before")
    @classmethod
    def normalize_url(cls, value: object) -> object:
        if isinstance(value, str):
            return normalize_livekit_url(value)
        return value

    @field_validator("livekit_api_key", "livekit_api_secret", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class LiveKitSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configured: bool
    livekit_url: str | None = Field(default=None, min_length=1, max_length=2048)


@dataclass(frozen=True, slots=True)
class LiveKitRuntimeConfig:
    credentials: ResolvedLiveKitCredentials


def create_livekit_router(
    auth_dependency: AuthenticatedUserDependency,
    token_service: LiveKitVoiceTokenService,
    session_factory: AsyncSessionFactory | None = None,
    secret_vault: UserSecretVaultService | None = None,
    voice_settings: VoiceAgentSettings | None = None,
    call_router: LiveKitCallRouting | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/livekit", tags=["livekit"])
    resolved_voice_settings = voice_settings or VoiceAgentSettings()
    if resolved_voice_settings.voice_route_signing_secret is None and token_service.route_signer is not None:
        resolved_voice_settings = resolved_voice_settings.model_copy(
            update={"voice_route_signing_secret": token_service.route_signer.secret},
        )
    resolved_call_router = call_router
    if resolved_call_router is None and session_factory is not None:
        resolved_call_router = LiveKitCallRouter(
            session_factory=session_factory,
            settings=resolved_voice_settings,
            token_template=token_service,
        )

    async def authenticated_user(request: Request) -> AuthenticatedUser:
        return await auth_dependency(request)

    def scoped_session_factory_for(user_id: str) -> AsyncSessionFactory:
        if session_factory is None:
            message = "session factory is not configured"
            raise RuntimeError(message)
        return tenant_scoped_session_factory(
            session_factory=session_factory,
            user_id=user_id,
        )

    async def livekit_runtime_config(
        current_user: AuthenticatedUser,
    ) -> LiveKitRuntimeConfig:
        if session_factory is None or secret_vault is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LiveKit is not configured",
            )

        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                saved_settings = await UserLiveKitSettingsService(
                    session=session,
                    secret_vault=secret_vault,
                ).resolve_settings(current_user.user_id)
        except (
            InvalidLiveKitCredentialsError,
            MissingLiveKitCredentialsError,
        ) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LiveKit is not configured",
            ) from exc
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LiveKit settings are unavailable",
            ) from exc

        return LiveKitRuntimeConfig(
            credentials=saved_settings,
        )

    def require_livekit_url(value: str | None) -> str:
        if value is None or value.strip() == "":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LiveKit is not configured",
            )
        return value.strip()

    async def resolve_route_context(
        current_user: AuthenticatedUser,
        requested_agent_id: str | None,
    ) -> AgentRouteContext | None:
        if session_factory is None:
            return None
        try:
            return await AgentRouteResolver(
                session_factory=scoped_session_factory_for(current_user.user_id),
            ).resolve_livekit_route(
                owner_user_id=current_user.user_id,
                agent_id=requested_agent_id,
            )
        except AgentIdentityNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent was not found",
            ) from exc
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Agent routing is unavailable",
            ) from exc

    @router.get("/settings", response_model=LiveKitSettingsResponse)
    async def get_settings(
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> LiveKitSettingsResponse:
        if session_factory is None or secret_vault is None:
            return LiveKitSettingsResponse(configured=False, livekit_url=None)

        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                summary = await UserLiveKitSettingsService(
                    session=session,
                    secret_vault=secret_vault,
                ).get_summary(current_user.user_id)
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LiveKit settings are unavailable",
            ) from exc

        return LiveKitSettingsResponse(
            configured=summary.configured,
            livekit_url=summary.livekit_url,
        )

    @router.put("/settings", response_model=LiveKitSettingsResponse)
    async def put_settings(
        request_body: LiveKitSettingsRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> LiveKitSettingsResponse:
        if session_factory is None or secret_vault is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LiveKit settings are unavailable",
            )

        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                summary = await UserLiveKitSettingsService(
                    session=session,
                    secret_vault=secret_vault,
                ).set_settings(
                    user_id=current_user.user_id,
                    livekit_url=request_body.livekit_url,
                    api_key=request_body.livekit_api_key,
                    api_secret=request_body.livekit_api_secret,
                )
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LiveKit settings are unavailable",
            ) from exc

        return LiveKitSettingsResponse(
            configured=summary.configured,
            livekit_url=summary.livekit_url,
        )

    @router.post("/token", response_model=LiveKitTokenSourceResponse)
    async def create_token(
        request_body: LiveKitTokenSourceRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> LiveKitTokenSourceResponse:
        runtime_config = await livekit_runtime_config(current_user)
        server_url = require_livekit_url(runtime_config.credentials.livekit_url)
        route_context = await resolve_route_context(
            current_user=current_user,
            requested_agent_id=request_body.agent_id,
        )

        try:
            voice_request = request_body.to_voice_token_request(
                agent_id=route_context.agent.id if route_context is not None else None,
                runtime_session_id=(route_context.runtime_session_id if route_context is not None else None),
            )
            if resolved_call_router is None:
                reason_code = "worker_routing_unavailable"
                raise VoiceWorkerUnavailableError(reason_code)
            voice_token = await resolved_call_router.create_join_token(
                authenticated_user=current_user,
                credentials=runtime_config.credentials,
                request=voice_request,
                source="livekit_api",
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
        except MissingLiveKitCredentialsError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LiveKit is not configured",
            ) from exc
        except MissingVoiceRouteSignerError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LiveKit route signing is not configured",
            ) from exc
        except (VoiceWorkerRegistrationError, VoiceWorkerUnavailableError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LiveKit voice capacity is temporarily unavailable",
            ) from exc
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LiveKit voice routing is temporarily unavailable",
            ) from exc

        return LiveKitTokenSourceResponse(
            server_url=server_url,
            participant_token=voice_token.token,
            agent_id=route_context.agent.id if route_context is not None else None,
            runtime_session_id=(route_context.runtime_session_id if route_context is not None else None),
        )

    return router

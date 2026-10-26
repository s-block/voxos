from dataclasses import dataclass, field
from datetime import timedelta
from typing import Self

import orjson
from livekit import api
from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, field_validator

from voxos.auth.models import AuthenticatedUser
from voxos.voice.errors import MissingLiveKitCredentialsError, MissingVoiceRouteSignerError
from voxos.voice.route_signing import LiveKitRouteSigner
from voxos.voice.settings import VoiceAgentSettings

DEFAULT_AGENT_METADATA_KEY = "agent_id"
DEFAULT_RUNTIME_SESSION_METADATA_KEY = "runtime_session_id"
DEFAULT_ROUTE_METADATA_KEY = "voxos_route"


class LiveKitVoiceTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    room_name: str = Field(min_length=1, max_length=512)
    participant_identity: str | None = Field(default=None, min_length=1, max_length=128)
    participant_name: str | None = Field(default=None, min_length=1)
    client_metadata: dict[str, JsonValue] = Field(default_factory=dict)
    agent_name: str | None = Field(default=None, min_length=1)
    agent_id: str | None = Field(default=None, min_length=1, max_length=36)
    runtime_session_id: str | None = Field(default=None, min_length=1, max_length=36)

    @field_validator(
        "room_name",
        "participant_identity",
        "participant_name",
        "agent_name",
        "agent_id",
        "runtime_session_id",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class LiveKitVoiceToken(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token: str = Field(min_length=1, repr=False)
    identity: str = Field(min_length=1)
    room_name: str = Field(min_length=1)
    metadata: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class ResolvedLiveKitCredentials:
    livekit_url: str = field(repr=False)
    api_key: str = field(repr=False)
    api_secret: SecretStr = field(repr=False)


@dataclass(frozen=True, slots=True)
class LiveKitIdentityMetadataBuilder:
    user_metadata_key: str
    agent_metadata_key: str = DEFAULT_AGENT_METADATA_KEY
    runtime_session_metadata_key: str = DEFAULT_RUNTIME_SESSION_METADATA_KEY
    route_metadata_key: str = DEFAULT_ROUTE_METADATA_KEY

    def build_payload(
        self,
        authenticated_user: AuthenticatedUser,
        client_metadata: dict[str, JsonValue] | None = None,
        agent_id: str | None = None,
        runtime_session_id: str | None = None,
        route_token: str | None = None,
    ) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {}
        reserved_keys = {
            self.user_metadata_key,
            self.agent_metadata_key,
            self.runtime_session_metadata_key,
            self.route_metadata_key,
        }
        if client_metadata is not None:
            payload.update(
                {key: value for key, value in client_metadata.items() if key not in reserved_keys},
            )
        payload[self.user_metadata_key] = authenticated_user.user_id
        if agent_id is not None:
            payload[self.agent_metadata_key] = agent_id
        if runtime_session_id is not None:
            payload[self.runtime_session_metadata_key] = runtime_session_id
        if route_token is not None:
            payload[self.route_metadata_key] = route_token
        return payload

    def build_metadata(
        self,
        authenticated_user: AuthenticatedUser,
        client_metadata: dict[str, JsonValue] | None = None,
        agent_id: str | None = None,
        runtime_session_id: str | None = None,
        route_token: str | None = None,
    ) -> str:
        payload = self.build_payload(
            authenticated_user=authenticated_user,
            client_metadata=client_metadata,
            agent_id=agent_id,
            runtime_session_id=runtime_session_id,
            route_token=route_token,
        )
        return orjson.dumps(payload).decode()


@dataclass(frozen=True, slots=True)
class LiveKitVoiceTokenService:
    api_key: str | None = field(repr=False)
    api_secret: SecretStr | None = field(repr=False)
    metadata_builder: LiveKitIdentityMetadataBuilder
    ttl_seconds: int
    route_signer: LiveKitRouteSigner | None = None

    @classmethod
    def from_settings(cls, settings: VoiceAgentSettings) -> Self:
        return cls(
            api_key=None,
            api_secret=None,
            metadata_builder=LiveKitIdentityMetadataBuilder(
                user_metadata_key=settings.voice_user_metadata_key,
                route_metadata_key=settings.voice_route_metadata_key,
            ),
            ttl_seconds=settings.voice_token_ttl_seconds,
            route_signer=(
                None
                if settings.voice_route_signing_secret is None
                else LiveKitRouteSigner(
                    secret=settings.voice_route_signing_secret,
                    ttl_seconds=settings.voice_token_ttl_seconds,
                    clock_skew_seconds=settings.voice_route_clock_skew_seconds,
                )
            ),
        )

    def create_join_token(
        self,
        authenticated_user: AuthenticatedUser,
        request: LiveKitVoiceTokenRequest,
    ) -> LiveKitVoiceToken:
        if self.api_key is None or self.api_secret is None:
            raise MissingLiveKitCredentialsError

        route_token = self._route_token_for_request(
            authenticated_user=authenticated_user,
            request=request,
        )
        metadata = self.metadata_builder.build_metadata(
            authenticated_user=authenticated_user,
            client_metadata=request.client_metadata,
            agent_id=request.agent_id,
            runtime_session_id=request.runtime_session_id,
            route_token=route_token,
        )
        access_token = (
            api.AccessToken(
                api_key=self.api_key,
                api_secret=self.api_secret.get_secret_value(),
            )
            .with_identity(request.participant_identity or authenticated_user.user_id)
            .with_name(request.participant_name or authenticated_user.user_id)
            .with_metadata(metadata)
            .with_ttl(timedelta(seconds=self.ttl_seconds))
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=request.room_name,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                ),
            )
        )

        if request.agent_name is not None:
            access_token = access_token.with_room_config(
                api.RoomConfiguration(
                    agents=[
                        api.RoomAgentDispatch(
                            agent_name=request.agent_name,
                            metadata=metadata,
                        ),
                    ],
                ),
            )

        token = access_token.to_jwt()
        return LiveKitVoiceToken(
            token=token,
            identity=request.participant_identity or authenticated_user.user_id,
            room_name=request.room_name,
            metadata=metadata,
        )

    def _route_token_for_request(
        self,
        *,
        authenticated_user: AuthenticatedUser,
        request: LiveKitVoiceTokenRequest,
    ) -> str | None:
        if request.agent_name is None:
            return None
        if self.route_signer is None:
            raise MissingVoiceRouteSignerError
        if request.agent_id is None or request.runtime_session_id is None:
            raise MissingVoiceRouteSignerError
        return self.route_signer.sign(
            user_id=authenticated_user.user_id,
            agent_id=request.agent_id,
            runtime_session_id=request.runtime_session_id,
            room_name=request.room_name,
        )

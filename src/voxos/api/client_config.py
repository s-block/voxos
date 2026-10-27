from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from voxos.auth.settings import FirebaseAuthSettings
from voxos.notifications.settings import PushNotificationSettings
from voxos.voice.settings import VoiceAgentSettings

CLIENT_CONFIG_UNAVAILABLE = "Client configuration is incomplete"
LIVEKIT_TOKEN_ENDPOINT_PATH: Literal["/api/livekit/token"] = "/api/livekit/token"


class ClientConfigSettings(BaseSettings):
    """Public Firebase application identifiers used by client applications."""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    firebase_api_key: str | None = Field(default=None, alias="FIREBASE_API_KEY")
    firebase_messaging_sender_id: str | None = Field(
        default=None,
        alias="FIREBASE_MESSAGING_SENDER_ID",
    )
    firebase_android_app_id: str | None = Field(
        default=None,
        alias="FIREBASE_ANDROID_APP_ID",
    )
    firebase_ios_app_id: str | None = Field(
        default=None,
        alias="FIREBASE_IOS_APP_ID",
    )
    firebase_web_app_id: str | None = Field(
        default=None,
        alias="FIREBASE_WEB_APP_ID",
    )
    firebase_google_ios_client_id: str | None = Field(
        default=None,
        alias="FIREBASE_GOOGLE_IOS_CLIENT_ID",
    )
    firebase_google_server_client_id: str | None = Field(
        default=None,
        alias="FIREBASE_GOOGLE_SERVER_CLIENT_ID",
    )
    firebase_auth_domain: str | None = Field(
        default=None,
        alias="FIREBASE_AUTH_DOMAIN",
    )
    firebase_storage_bucket: str | None = Field(
        default=None,
        alias="FIREBASE_STORAGE_BUCKET",
    )

    @field_validator("*", mode="before")
    @classmethod
    def strip_optional_value(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class FirebaseClientConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    messaging_sender_id: str = Field(min_length=1)
    android_app_id: str = Field(min_length=1)
    ios_app_id: str = Field(min_length=1)
    web_app_id: str | None = Field(default=None, min_length=1)
    google_ios_client_id: str = Field(min_length=1)
    google_server_client_id: str = Field(min_length=1)
    auth_domain: str | None = Field(default=None, min_length=1)
    storage_bucket: str | None = Field(default=None, min_length=1)


class VoiceClientConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_name: str = Field(min_length=1)
    token_endpoint_path: Literal["/api/livekit/token"] = LIVEKIT_TOKEN_ENDPOINT_PATH


class PushClientConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool


class ClientConfigResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    firebase: FirebaseClientConfig
    push: PushClientConfig
    voice: VoiceClientConfig


def create_client_config_router(
    *,
    client_settings: ClientConfigSettings,
    firebase_settings: FirebaseAuthSettings,
    voice_settings: VoiceAgentSettings,
    push_settings: PushNotificationSettings | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/client-config", tags=["client-config"])
    resolved_push_settings = push_settings or PushNotificationSettings()

    @router.get(
        "",
        response_model=ClientConfigResponse,
        response_model_exclude_none=True,
    )
    async def get_client_config() -> ClientConfigResponse:
        missing_settings = _missing_required_settings(
            client_settings=client_settings,
            firebase_settings=firebase_settings,
            voice_settings=voice_settings,
        )
        if missing_settings:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(f"{CLIENT_CONFIG_UNAVAILABLE}; missing required settings: {', '.join(missing_settings)}"),
            )

        return ClientConfigResponse(
            firebase=FirebaseClientConfig(
                project_id=firebase_settings.firebase_project_id.strip(),
                api_key=_required(client_settings.firebase_api_key),
                messaging_sender_id=_required(
                    client_settings.firebase_messaging_sender_id,
                ),
                android_app_id=_required(client_settings.firebase_android_app_id),
                ios_app_id=_required(client_settings.firebase_ios_app_id),
                web_app_id=client_settings.firebase_web_app_id,
                google_ios_client_id=_required(
                    client_settings.firebase_google_ios_client_id,
                ),
                google_server_client_id=_required(
                    client_settings.firebase_google_server_client_id,
                ),
                auth_domain=client_settings.firebase_auth_domain,
                storage_bucket=client_settings.firebase_storage_bucket,
            ),
            push=PushClientConfig(enabled=resolved_push_settings.enabled),
            voice=VoiceClientConfig(
                agent_name=voice_settings.voice_agent_name.strip(),
            ),
        )

    return router


def _missing_required_settings(
    *,
    client_settings: ClientConfigSettings,
    firebase_settings: FirebaseAuthSettings,
    voice_settings: VoiceAgentSettings,
) -> tuple[str, ...]:
    required_settings = (
        ("FIREBASE_PROJECT_ID", firebase_settings.firebase_project_id),
        ("FIREBASE_API_KEY", client_settings.firebase_api_key),
        (
            "FIREBASE_MESSAGING_SENDER_ID",
            client_settings.firebase_messaging_sender_id,
        ),
        ("FIREBASE_ANDROID_APP_ID", client_settings.firebase_android_app_id),
        ("FIREBASE_IOS_APP_ID", client_settings.firebase_ios_app_id),
        (
            "FIREBASE_GOOGLE_IOS_CLIENT_ID",
            client_settings.firebase_google_ios_client_id,
        ),
        (
            "FIREBASE_GOOGLE_SERVER_CLIENT_ID",
            client_settings.firebase_google_server_client_id,
        ),
        ("VOICE_AGENT_NAME", voice_settings.voice_agent_name),
    )
    return tuple(setting_name for setting_name, value in required_settings if value is None or value.strip() == "")


def _required(value: str | None) -> str:
    if value is None:
        raise AssertionError
    return value

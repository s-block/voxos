from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy.exc import SQLAlchemyError

from voxos.auth.models import AuthenticatedUser
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory
from voxos.logos.domains import logo_for_voice_provider
from voxos.logos.models import BrandLogo
from voxos.providers.model_catalog import (
    GOOGLE_GEMINI_LIVE_MODEL_OPTIONS,
    GROK_VOICE_MODEL_OPTIONS,
    OPENAI_REALTIME_MODEL_OPTIONS,
    ProviderModelOption,
    model_ids,
)
from voxos.providers.models import (
    DEFAULT_PREFERRED_LANGUAGE,
    RealtimeVoiceConfig,
    RealtimeVoiceProvider,
)
from voxos.user_settings.models import UserProviderCredentialRecord
from voxos.user_settings.services import (
    UserCredentialService,
    UserCredentialSummary,
    UserVoicePreferenceService,
)
from voxos.voice.errors import MissingVoiceCredentialError, VoicePreferencesNotFoundError
from voxos.voice.models import UserVoicePreferences

AuthenticatedUserDependency = Callable[[Request], Awaitable[AuthenticatedUser]]

DEFAULT_MODEL = "gpt-realtime-2.1-mini"
DEFAULT_VOICE = "marin"
DEFAULT_AGENT_INSTRUCTIONS = "You are a helpful voice assistant."
DEFAULT_INITIAL_REPLY_INSTRUCTIONS = "Greet the user and offer your assistance."
OPENAI_REALTIME_MODELS = model_ids(OPENAI_REALTIME_MODEL_OPTIONS)
OPENAI_REALTIME_VOICES = (
    DEFAULT_VOICE,
    "cedar",
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "sage",
    "shimmer",
    "verse",
)
GOOGLE_GEMINI_LIVE_MODELS = model_ids(GOOGLE_GEMINI_LIVE_MODEL_OPTIONS)
GOOGLE_GEMINI_LIVE_VOICES = (
    "Puck",
    "Achernar",
    "Achird",
    "Algenib",
    "Algieba",
    "Alnilam",
    "Aoede",
    "Autonoe",
    "Callirrhoe",
    "Charon",
    "Despina",
    "Enceladus",
    "Erinome",
    "Fenrir",
    "Gacrux",
    "Iapetus",
    "Kore",
    "Laomedeia",
    "Leda",
    "Orus",
    "Pulcherrima",
    "Rasalgethi",
    "Sadachbia",
    "Sadaltager",
    "Schedar",
    "Sulafat",
    "Umbriel",
    "Vindemiatrix",
    "Zephyr",
    "Zubenelgenubi",
)
GROK_VOICE_AGENT_MODELS = model_ids(GROK_VOICE_MODEL_OPTIONS)
GROK_VOICE_AGENT_VOICES = ("Ara", "Eve", "Leo", "Rex", "Sal")


class VoiceProviderDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: RealtimeVoiceProvider
    label: str = Field(min_length=1)
    api_key_label: str = Field(min_length=1)
    default_model: str = Field(min_length=1)
    default_voice: str = Field(min_length=1)
    suggested_models: tuple[str, ...] = Field(default_factory=tuple)
    model_options: tuple[ProviderModelOption, ...] = Field(default_factory=tuple)
    suggested_voices: tuple[str, ...] = Field(default_factory=tuple)
    runtime_supported: bool
    library: str = Field(min_length=1)
    docs_url: str = Field(min_length=1)
    note: str = Field(min_length=1)
    logo: BrandLogo


PROVIDER_DEFINITIONS: dict[RealtimeVoiceProvider, VoiceProviderDefinition] = {
    RealtimeVoiceProvider.OPENAI_REALTIME: VoiceProviderDefinition(
        provider=RealtimeVoiceProvider.OPENAI_REALTIME,
        label="OpenAI",
        api_key_label="OpenAI API key",
        default_model=DEFAULT_MODEL,
        default_voice=DEFAULT_VOICE,
        suggested_models=OPENAI_REALTIME_MODELS,
        model_options=OPENAI_REALTIME_MODEL_OPTIONS,
        suggested_voices=OPENAI_REALTIME_VOICES,
        runtime_supported=True,
        library="livekit-agents[openai]",
        docs_url="https://docs.livekit.io/agents/models/realtime/plugins/openai/",
        note="Runs through the official LiveKit OpenAI Realtime plugin.",
        logo=logo_for_voice_provider(RealtimeVoiceProvider.OPENAI_REALTIME.value),
    ),
    RealtimeVoiceProvider.GOOGLE_GEMINI_LIVE: VoiceProviderDefinition(
        provider=RealtimeVoiceProvider.GOOGLE_GEMINI_LIVE,
        label="Google",
        api_key_label="Google API key",
        default_model="gemini-3.1-flash-live-preview",
        default_voice="Puck",
        suggested_models=GOOGLE_GEMINI_LIVE_MODELS,
        model_options=GOOGLE_GEMINI_LIVE_MODEL_OPTIONS,
        suggested_voices=GOOGLE_GEMINI_LIVE_VOICES,
        runtime_supported=True,
        library="livekit-agents[google]",
        docs_url="https://docs.livekit.io/agents/models/realtime/plugins/gemini/",
        note="Runs through the official LiveKit Google realtime plugin.",
        logo=logo_for_voice_provider(RealtimeVoiceProvider.GOOGLE_GEMINI_LIVE.value),
    ),
    RealtimeVoiceProvider.HUME_EVI3: VoiceProviderDefinition(
        provider=RealtimeVoiceProvider.HUME_EVI3,
        label="Hume",
        api_key_label="Hume API key",
        default_model="evi-3",
        default_voice="default",
        suggested_models=("evi-3",),
        suggested_voices=("default",),
        runtime_supported=False,
        library="hume Python SDK",
        docs_url="https://dev.hume.ai/docs/speech-to-speech-evi/quickstart/python",
        note="Credential storage is ready; the LiveKit worker needs a Hume EVI adapter.",
        logo=logo_for_voice_provider(RealtimeVoiceProvider.HUME_EVI3.value),
    ),
    RealtimeVoiceProvider.ELEVENLABS_CONVERSATIONAL_AI: VoiceProviderDefinition(
        provider=RealtimeVoiceProvider.ELEVENLABS_CONVERSATIONAL_AI,
        label="ElevenLabs",
        api_key_label="ElevenLabs API key",
        default_model="elevenlabs-conversational-ai",
        default_voice="default",
        suggested_models=("elevenlabs-conversational-ai",),
        suggested_voices=("default",),
        runtime_supported=False,
        library="elevenlabs Python SDK or ElevenAgents WebSocket API",
        docs_url="https://elevenlabs.io/docs/eleven-agents/overview",
        note="Credential storage is ready; the worker needs an ElevenAgents bridge.",
        logo=logo_for_voice_provider(
            RealtimeVoiceProvider.ELEVENLABS_CONVERSATIONAL_AI.value,
        ),
    ),
    RealtimeVoiceProvider.MISTRAL_VOXTRAL: VoiceProviderDefinition(
        provider=RealtimeVoiceProvider.MISTRAL_VOXTRAL,
        label="Mistral",
        api_key_label="Mistral API key",
        default_model="voxtral-mini-latest",
        default_voice="default",
        suggested_models=("voxtral-mini-latest",),
        suggested_voices=("default",),
        runtime_supported=False,
        library="mistralai Python SDK and Voxtral audio APIs",
        docs_url="https://docs.mistral.ai/studio-api/audio/speech_to_text",
        note="Credential storage is ready; Voxtral is not a drop-in realtime voice agent.",
        logo=logo_for_voice_provider(RealtimeVoiceProvider.MISTRAL_VOXTRAL.value),
    ),
    RealtimeVoiceProvider.GROK_VOICE_AGENT: VoiceProviderDefinition(
        provider=RealtimeVoiceProvider.GROK_VOICE_AGENT,
        label="xAI",
        api_key_label="xAI API key",
        default_model="grok-voice-think-fast-1.0",
        default_voice="Ara",
        suggested_models=GROK_VOICE_AGENT_MODELS,
        model_options=GROK_VOICE_MODEL_OPTIONS,
        suggested_voices=GROK_VOICE_AGENT_VOICES,
        runtime_supported=True,
        library="livekit-agents[xai]",
        docs_url="https://docs.livekit.io/agents/models/realtime/plugins/xai/",
        note="Runs through the official LiveKit xAI realtime plugin.",
        logo=logo_for_voice_provider(RealtimeVoiceProvider.GROK_VOICE_AGENT.value),
    ),
}


class OpenAIVoiceCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    openai_api_key: SecretStr = Field(min_length=1)

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class VoiceProviderCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: RealtimeVoiceProvider
    api_key: SecretStr = Field(min_length=1)
    activate: bool = True

    @field_validator("api_key", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class ActiveVoiceProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: RealtimeVoiceProvider


class OpenAIVoicePreferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: RealtimeVoiceProvider = RealtimeVoiceProvider.OPENAI_REALTIME
    model: str = Field(default=DEFAULT_MODEL, min_length=1)
    voice: str = Field(default=DEFAULT_VOICE, min_length=1)
    preferred_language: str = Field(
        default=DEFAULT_PREFERRED_LANGUAGE,
        min_length=1,
        max_length=128,
    )
    agent_instructions: str = Field(
        default=DEFAULT_AGENT_INSTRUCTIONS,
        min_length=1,
    )
    initial_reply_instructions: str | None = Field(
        default=DEFAULT_INITIAL_REPLY_INSTRUCTIONS,
        min_length=1,
    )

    @field_validator(
        "model",
        "voice",
        "preferred_language",
        "agent_instructions",
        "initial_reply_instructions",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class OpenAIVoiceSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configured: bool
    provider: RealtimeVoiceProvider
    model: str
    voice: str
    preferred_language: str = Field(min_length=1, max_length=128)
    agent_instructions: str
    initial_reply_instructions: str | None


class VoiceProviderSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: RealtimeVoiceProvider
    label: str
    api_key_label: str
    configured: bool
    active: bool
    runtime_supported: bool
    default_model: str
    default_voice: str
    suggested_models: tuple[str, ...]
    model_options: tuple[ProviderModelOption, ...] = Field(default_factory=tuple)
    suggested_voices: tuple[str, ...]
    credential_last_four: str | None
    library: str
    docs_url: str
    note: str
    logo: BrandLogo


class VoiceProviderSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    active_provider: RealtimeVoiceProvider
    active_settings: OpenAIVoiceSettingsResponse
    providers: tuple[VoiceProviderSummaryResponse, ...]


def create_voice_settings_router(
    auth_dependency: AuthenticatedUserDependency,
    session_factory: AsyncSessionFactory,
    secret_vault: UserSecretVaultService,
) -> APIRouter:
    router = APIRouter(prefix="/api/voice", tags=["voice"])

    async def authenticated_user(request: Request) -> AuthenticatedUser:
        return await auth_dependency(request)

    def scoped_session_factory_for(user_id: str) -> AsyncSessionFactory:
        return tenant_scoped_session_factory(
            session_factory=session_factory,
            user_id=user_id,
        )

    @router.get("/settings", response_model=OpenAIVoiceSettingsResponse)
    async def get_settings(
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> OpenAIVoiceSettingsResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                preference_service = UserVoicePreferenceService(session)
                preferences = await preference_service.get_preferences(current_user.user_id)
                credential_service = UserCredentialService(
                    session=session,
                    secret_vault=secret_vault,
                )
                try:
                    credential = await credential_service.get_active_credential_record(
                        user_id=current_user.user_id,
                        provider=preferences.provider,
                        credential_ref=preferences.credential_ref,
                    )
                except MissingVoiceCredentialError:
                    credential = None
        except VoicePreferencesNotFoundError:
            return default_settings_response()
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Voice settings are unavailable",
            ) from exc

        return response_from_preferences(preferences, credential)

    @router.get("/provider-settings", response_model=VoiceProviderSettingsResponse)
    async def get_provider_settings(
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> VoiceProviderSettingsResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                preference_service = UserVoicePreferenceService(session)
                credential_service = UserCredentialService(
                    session=session,
                    secret_vault=secret_vault,
                )
                preferences = await existing_preferences(
                    user_id=current_user.user_id,
                    preference_service=preference_service,
                )
                active_credential = await active_credential_for_preferences(
                    user_id=current_user.user_id,
                    preferences=preferences,
                    credential_service=credential_service,
                )
                credential_summaries = await credential_service.list_active_credential_summaries(
                    current_user.user_id,
                )
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Voice settings are unavailable",
            ) from exc

        return provider_settings_response(
            preferences=preferences,
            active_credential=active_credential,
            credential_summaries=credential_summaries,
        )

    @router.put("/settings", response_model=OpenAIVoiceSettingsResponse)
    async def put_settings(
        request_body: OpenAIVoicePreferencesRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> OpenAIVoiceSettingsResponse:
        provider = request_body.provider
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                preference_service = UserVoicePreferenceService(session)
                credential_service = UserCredentialService(
                    session=session,
                    secret_vault=secret_vault,
                )
                current_preferences = await existing_preferences(
                    user_id=current_user.user_id,
                    preference_service=preference_service,
                )
                credential = await preferred_credential_for_provider(
                    user_id=current_user.user_id,
                    provider=provider,
                    current_preferences=current_preferences,
                    credential_service=credential_service,
                )
                preferences = UserVoicePreferences(
                    user_id=current_user.user_id,
                    provider=provider,
                    model=request_body.model,
                    voice=request_body.voice,
                    credential_ref=credential.id,
                    provider_config=RealtimeVoiceConfig(
                        agent_instructions=request_body.agent_instructions,
                        preferred_language=request_body.preferred_language,
                        initial_reply_instructions=(request_body.initial_reply_instructions),
                    ),
                )
                await preference_service.set_preferences(preferences)
        except (MissingVoiceCredentialError, VoicePreferencesNotFoundError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=missing_voice_key_detail(provider),
            ) from exc
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Voice settings are unavailable",
            ) from exc

        return response_from_preferences(preferences, credential)

    @router.put("/settings/credential", response_model=OpenAIVoiceSettingsResponse)
    async def put_credential(
        request_body: OpenAIVoiceCredentialRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> OpenAIVoiceSettingsResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                preference_service = UserVoicePreferenceService(session)
                credential_service = UserCredentialService(
                    session=session,
                    secret_vault=secret_vault,
                )
                preferences, credential = await save_provider_credential(
                    user_id=current_user.user_id,
                    provider=RealtimeVoiceProvider.OPENAI_REALTIME,
                    secret=request_body.openai_api_key,
                    activate=True,
                    preference_service=preference_service,
                    credential_service=credential_service,
                )
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Voice settings are unavailable",
            ) from exc

        if preferences is None:
            message = "Saving the default voice credential did not create preferences"
            raise RuntimeError(message)
        return response_from_preferences(preferences, credential)

    @router.put("/settings/provider-credential", response_model=VoiceProviderSettingsResponse)
    async def put_provider_credential(
        request_body: VoiceProviderCredentialRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> VoiceProviderSettingsResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                preference_service = UserVoicePreferenceService(session)
                credential_service = UserCredentialService(
                    session=session,
                    secret_vault=secret_vault,
                )
                preferences, credential = await save_provider_credential(
                    user_id=current_user.user_id,
                    provider=request_body.provider,
                    secret=request_body.api_key,
                    activate=request_body.activate,
                    preference_service=preference_service,
                    credential_service=credential_service,
                )
                credential_summaries = await credential_service.list_active_credential_summaries(
                    current_user.user_id,
                )
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Voice settings are unavailable",
            ) from exc

        return provider_settings_response(
            preferences=preferences,
            active_credential=credential,
            credential_summaries=credential_summaries,
        )

    @router.put("/settings/active-provider", response_model=VoiceProviderSettingsResponse)
    async def put_active_provider(
        request_body: ActiveVoiceProviderRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> VoiceProviderSettingsResponse:
        provider = request_body.provider
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                preference_service = UserVoicePreferenceService(session)
                credential_service = UserCredentialService(
                    session=session,
                    secret_vault=secret_vault,
                )
                current_preferences = await existing_preferences(
                    user_id=current_user.user_id,
                    preference_service=preference_service,
                )
                credential = await credential_service.get_latest_active_credential_record(
                    user_id=current_user.user_id,
                    provider=provider,
                )
                preferences = preferences_for_credential(
                    user_id=current_user.user_id,
                    provider=provider,
                    credential_ref=credential.id,
                    current_preferences=current_preferences,
                )
                await preference_service.set_preferences(preferences)
                credential_summaries = await credential_service.list_active_credential_summaries(
                    current_user.user_id,
                )
        except MissingVoiceCredentialError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Save a {provider_label(provider)} key before switching providers",
            ) from exc
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Voice settings are unavailable",
            ) from exc

        return provider_settings_response(
            preferences=preferences,
            active_credential=credential,
            credential_summaries=credential_summaries,
        )

    return router


async def existing_preferences(
    user_id: str,
    preference_service: UserVoicePreferenceService,
) -> UserVoicePreferences | None:
    try:
        return await preference_service.get_preferences(user_id)
    except VoicePreferencesNotFoundError:
        return None


def provider_definition(provider: RealtimeVoiceProvider) -> VoiceProviderDefinition:
    return PROVIDER_DEFINITIONS[provider]


def provider_label(provider: RealtimeVoiceProvider) -> str:
    return provider_definition(provider).label


def missing_voice_key_detail(provider: RealtimeVoiceProvider) -> str:
    if provider == RealtimeVoiceProvider.OPENAI_REALTIME:
        return "Save an OpenAI key before saving voice preferences"
    return f"Save a {provider_label(provider)} key before saving voice preferences"


async def active_credential_for_preferences(
    user_id: str,
    preferences: UserVoicePreferences | None,
    credential_service: UserCredentialService,
) -> UserProviderCredentialRecord | None:
    if preferences is None:
        return None

    try:
        return await credential_service.get_active_credential_record(
            user_id=user_id,
            provider=preferences.provider,
            credential_ref=preferences.credential_ref,
        )
    except MissingVoiceCredentialError:
        return None


async def preferred_credential_for_provider(
    user_id: str,
    provider: RealtimeVoiceProvider,
    current_preferences: UserVoicePreferences | None,
    credential_service: UserCredentialService,
) -> UserProviderCredentialRecord:
    if current_preferences is not None and current_preferences.provider == provider:
        try:
            return await credential_service.get_active_credential_record(
                user_id=user_id,
                provider=provider,
                credential_ref=current_preferences.credential_ref,
            )
        except MissingVoiceCredentialError:
            pass

    return await credential_service.get_latest_active_credential_record(
        user_id=user_id,
        provider=provider,
    )


async def save_provider_credential(
    user_id: str,
    provider: RealtimeVoiceProvider,
    secret: SecretStr,
    activate: bool,
    preference_service: UserVoicePreferenceService,
    credential_service: UserCredentialService,
) -> tuple[UserVoicePreferences | None, UserProviderCredentialRecord | None]:
    current_preferences = await existing_preferences(
        user_id=user_id,
        preference_service=preference_service,
    )
    credential = await credential_service.create_credential(
        user_id=user_id,
        provider=provider,
        label=provider_label(provider),
        secret=secret,
    )
    await credential_service.revoke_active_credentials_for_provider(
        user_id=user_id,
        provider=provider,
        except_credential_ref=credential.id,
    )

    if activate:
        preferences = preferences_for_credential(
            user_id=user_id,
            provider=provider,
            credential_ref=credential.id,
            current_preferences=current_preferences,
        )
        await preference_service.set_preferences(preferences)
        return preferences, credential

    if current_preferences is not None and current_preferences.provider == provider:
        preferences = current_preferences.model_copy(
            update={"credential_ref": credential.id},
        )
        await preference_service.set_preferences(preferences)
        return preferences, credential

    active_credential = await active_credential_for_preferences(
        user_id=user_id,
        preferences=current_preferences,
        credential_service=credential_service,
    )
    return current_preferences, active_credential


def preferences_for_credential(
    user_id: str,
    provider: RealtimeVoiceProvider,
    credential_ref: str,
    current_preferences: UserVoicePreferences | None,
) -> UserVoicePreferences:
    if current_preferences is not None and current_preferences.provider == provider:
        return current_preferences.model_copy(
            update={"credential_ref": credential_ref},
        )

    definition = provider_definition(provider)
    return UserVoicePreferences(
        user_id=user_id,
        provider=provider,
        model=definition.default_model,
        voice=definition.default_voice,
        credential_ref=credential_ref,
        provider_config=RealtimeVoiceConfig(
            agent_instructions=DEFAULT_AGENT_INSTRUCTIONS,
            preferred_language=DEFAULT_PREFERRED_LANGUAGE,
            initial_reply_instructions=DEFAULT_INITIAL_REPLY_INSTRUCTIONS,
        ),
    )


def default_settings_response() -> OpenAIVoiceSettingsResponse:
    definition = provider_definition(RealtimeVoiceProvider.OPENAI_REALTIME)
    return OpenAIVoiceSettingsResponse(
        configured=False,
        provider=definition.provider,
        model=definition.default_model,
        voice=definition.default_voice,
        preferred_language=DEFAULT_PREFERRED_LANGUAGE,
        agent_instructions=DEFAULT_AGENT_INSTRUCTIONS,
        initial_reply_instructions=DEFAULT_INITIAL_REPLY_INSTRUCTIONS,
    )


def response_from_preferences(
    preferences: UserVoicePreferences,
    credential: UserProviderCredentialRecord | None,
) -> OpenAIVoiceSettingsResponse:
    return OpenAIVoiceSettingsResponse(
        configured=credential is not None,
        provider=preferences.provider,
        model=preferences.model,
        voice=preferences.voice,
        preferred_language=preferences.provider_config.preferred_language,
        agent_instructions=preferences.provider_config.agent_instructions,
        initial_reply_instructions=(preferences.provider_config.initial_reply_instructions),
    )


def provider_settings_response(
    preferences: UserVoicePreferences | None,
    active_credential: UserProviderCredentialRecord | None,
    credential_summaries: tuple[UserCredentialSummary, ...],
) -> VoiceProviderSettingsResponse:
    active_provider = preferences.provider if preferences is not None else RealtimeVoiceProvider.OPENAI_REALTIME
    active_settings = (
        response_from_preferences(preferences, active_credential)
        if preferences is not None
        else default_settings_response()
    )
    credential_by_provider: dict[RealtimeVoiceProvider, UserCredentialSummary] = {}
    for summary in credential_summaries:
        credential_by_provider.setdefault(summary.provider, summary)

    return VoiceProviderSettingsResponse(
        active_provider=active_provider,
        active_settings=active_settings,
        providers=tuple(
            provider_summary_response(
                definition=definition,
                active_provider=active_provider,
                credential_summary=credential_by_provider.get(provider),
            )
            for provider, definition in PROVIDER_DEFINITIONS.items()
        ),
    )


def provider_summary_response(
    definition: VoiceProviderDefinition,
    active_provider: RealtimeVoiceProvider,
    credential_summary: UserCredentialSummary | None,
) -> VoiceProviderSummaryResponse:
    return VoiceProviderSummaryResponse(
        provider=definition.provider,
        label=definition.label,
        api_key_label=definition.api_key_label,
        configured=credential_summary is not None,
        active=definition.provider == active_provider,
        runtime_supported=definition.runtime_supported,
        default_model=definition.default_model,
        default_voice=definition.default_voice,
        suggested_models=definition.suggested_models,
        model_options=definition.model_options,
        suggested_voices=definition.suggested_voices,
        credential_last_four=(None if credential_summary is None else credential_summary.last_four),
        library=definition.library,
        docs_url=definition.docs_url,
        note=definition.note,
        logo=definition.logo,
    )

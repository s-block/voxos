from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from sqlalchemy.exc import SQLAlchemyError

from voxos.auth.models import AuthenticatedUser
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory
from voxos.image_generation.routing import displayed_image_generation_model
from voxos.logos.models import BrandLogo
from voxos.providers.model_catalog import (
    ProviderImageGenerationModelOption,
    ProviderModelOption,
)
from voxos.security.outbound_endpoints import (
    OutboundEndpointPolicy,
    OutboundEndpointPurpose,
)
from voxos.text.errors import MissingTextCredentialError, TextPreferencesNotFoundError
from voxos.text.models import (
    DEFAULT_TEXT_AGENT_INSTRUCTIONS,
    DEFAULT_TEXT_LANGUAGE,
    TextProvider,
    UserTextPreferences,
    default_text_provider_config,
    effective_allowed_subagent_models,
    effective_subagent_model,
    effective_text_model,
    text_provider_definition,
    validate_text_provider_temperature,
)
from voxos.text.service import default_text_preferences
from voxos.text.storage import (
    TextCredentialSummary,
    UserTextCredentialService,
    UserTextPreferenceService,
)
from voxos.user_settings.models import UserProviderCredentialRecord

AuthenticatedUserDependency = Callable[[Request], Awaitable[AuthenticatedUser]]


class TextProviderSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: TextProvider
    label: str
    api_key_label: str
    configured: bool
    active: bool
    runtime_supported: bool
    default_model: str
    default_multimodal_model: str | None
    default_browser_model: str | None
    suggested_models: tuple[str, ...] = Field(default_factory=tuple)
    model_options: tuple[ProviderModelOption, ...] = Field(default_factory=tuple)
    default_image_generation_model: str | None
    image_generation_model_options: tuple[
        ProviderImageGenerationModelOption,
        ...,
    ] = Field(default_factory=tuple)
    default_subagent_model: str = Field(min_length=1, max_length=256)
    allowed_subagent_models: tuple[str, ...] = Field(default_factory=tuple)
    credential_last_four: str | None
    base_url: str | None = None
    docs_url: str
    logo: BrandLogo


class TextSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configured: bool
    provider: TextProvider
    model: str = Field(min_length=1)
    preferred_language: str = Field(min_length=1, max_length=128)
    agent_instructions: str = Field(min_length=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    allowed_subagent_models: tuple[str, ...] = Field(default_factory=tuple)
    subagent_model: str = Field(min_length=1, max_length=256)
    image_generation_provider: TextProvider
    image_generation_model: str = Field(min_length=1, max_length=256)


class TextProviderSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    active_provider: TextProvider
    active_settings: TextSettingsResponse
    image_input_enabled: bool
    providers: tuple[TextProviderSummaryResponse, ...]


class TextProviderCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: TextProvider
    api_key: SecretStr = Field(min_length=1)
    activate: bool = True

    @field_validator("api_key", mode="before")
    @classmethod
    def strip_secret(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def reject_openai_compatible_provider(self) -> "TextProviderCredentialRequest":
        if self.provider == TextProvider.OPENAI_COMPATIBLE:
            message = "Configure OpenAI-compatible endpoints with the endpoint settings API"
            raise ValueError(message)
        return self


class OpenAICompatibleProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str = Field(min_length=1)
    available_models: tuple[str, ...] = Field(min_length=1, max_length=64)
    api_key: SecretStr | None = Field(default=None, min_length=1)

    @field_validator("base_url", mode="before")
    @classmethod
    def strip_base_url(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("available_models", mode="before")
    @classmethod
    def normalize_available_models(cls, value: object) -> object:
        if not isinstance(value, list | tuple):
            return value
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str):
                return value
            stripped = item.strip()
            if stripped and stripped not in normalized:
                normalized.append(stripped)
        return tuple(normalized)

    @field_validator("api_key", mode="before")
    @classmethod
    def strip_optional_secret(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class ActiveTextProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: TextProvider


class TextPreferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: TextProvider
    model: str = Field(min_length=1, max_length=256)
    preferred_language: str = Field(
        default=DEFAULT_TEXT_LANGUAGE,
        min_length=1,
        max_length=128,
    )
    agent_instructions: str = Field(
        default=DEFAULT_TEXT_AGENT_INSTRUCTIONS,
        min_length=1,
    )
    temperature: float | None = Field(default=None, ge=0, le=2)
    allowed_subagent_models: tuple[str, ...] = Field(default_factory=tuple)
    subagent_model: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator(
        "model",
        "preferred_language",
        "agent_instructions",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("allowed_subagent_models", mode="before")
    @classmethod
    def normalize_allowed_subagent_models(cls, value: object) -> object:
        if not isinstance(value, list | tuple):
            return value
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str):
                return value
            stripped = item.strip()
            if stripped and stripped not in normalized:
                normalized.append(stripped)
        return tuple(normalized)

    @model_validator(mode="after")
    def validate_subagent_models(self) -> "TextPreferencesRequest":
        validate_text_provider_temperature(self.provider, self.temperature)
        if self.provider == TextProvider.OPENAI_COMPATIBLE:
            if (
                self.subagent_model is not None
                and self.allowed_subagent_models
                and self.subagent_model not in self.allowed_subagent_models
            ):
                message = "Default subagent model must be in the allowed subagent model list"
                raise ValueError(message)
            return self
        supported_models = set(text_provider_definition(self.provider).available_models)
        if self.model not in supported_models:
            message = f"Model must be a current catalog model for {self.provider.value}: {self.model}"
            raise ValueError(message)
        unknown_models = set(self.effective_allowed_subagent_models()) - supported_models
        if unknown_models:
            message = (
                "Allowed subagent models must be current catalog models for the "
                f"selected provider: {', '.join(sorted(unknown_models))}"
            )
            raise ValueError(message)
        if self.effective_subagent_model() not in self.effective_allowed_subagent_models():
            message = "Default subagent model must be in the allowed subagent model list"
            raise ValueError(message)
        return self

    def effective_allowed_subagent_models(self) -> tuple[str, ...]:
        if self.allowed_subagent_models:
            return self.allowed_subagent_models
        return text_provider_definition(self.provider).available_models

    def effective_subagent_model(self) -> str:
        if self.subagent_model is not None:
            return self.subagent_model
        return text_provider_definition(self.provider).default_subagent_model


class ImageGenerationPreferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: TextProvider
    model: str = Field(min_length=1, max_length=256)

    @field_validator("model", mode="before")
    @classmethod
    def strip_model(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def validate_model(self) -> "ImageGenerationPreferencesRequest":
        supported_models = set(
            text_provider_definition(self.provider).available_image_generation_models,
        )
        if self.model not in supported_models:
            message = f"Image generation model must be a current catalog model for {self.provider.value}: {self.model}"
            raise ValueError(message)
        return self


def create_text_settings_router(
    auth_dependency: AuthenticatedUserDependency,
    session_factory: AsyncSessionFactory,
    secret_vault: UserSecretVaultService,
    image_input_enabled: bool = True,
    outbound_endpoint_policy: OutboundEndpointPolicy | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/text", tags=["text"])
    resolved_endpoint_policy = outbound_endpoint_policy or OutboundEndpointPolicy.from_settings()

    async def authenticated_user(request: Request) -> AuthenticatedUser:
        return await auth_dependency(request)

    def scoped_session_factory_for(user_id: str) -> AsyncSessionFactory:
        return tenant_scoped_session_factory(
            session_factory=session_factory,
            user_id=user_id,
        )

    @router.get("/provider-settings", response_model=TextProviderSettingsResponse)
    async def get_provider_settings(
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> TextProviderSettingsResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                preference_service = UserTextPreferenceService(session)
                credential_service = UserTextCredentialService(
                    session=session,
                    secret_vault=secret_vault,
                )
                preferences = await existing_preferences(
                    user_id=current_user.user_id,
                    preference_service=preference_service,
                )
                credential = await active_credential_for_preferences(
                    user_id=current_user.user_id,
                    preferences=preferences,
                    credential_service=credential_service,
                )
                summaries = await credential_service.list_active_credential_summaries(
                    current_user.user_id,
                )
        except SQLAlchemyError as exc:
            raise unavailable() from exc

        return provider_settings_response(
            preferences=preferences,
            active_credential=credential,
            credential_summaries=summaries,
            image_input_enabled=image_input_enabled,
        )

    @router.put("/settings", response_model=TextSettingsResponse)
    async def put_settings(
        request_body: TextPreferencesRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> TextSettingsResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                preference_service = UserTextPreferenceService(session)
                credential_service = UserTextCredentialService(
                    session=session,
                    secret_vault=secret_vault,
                )
                current_preferences = await existing_preferences(
                    user_id=current_user.user_id,
                    preference_service=preference_service,
                )
                allowed_subagent_models, subagent_model = validated_text_model_selection(
                    request_body,
                    current_preferences,
                )
                credential = await preferred_credential_for_provider(
                    user_id=current_user.user_id,
                    provider=request_body.provider,
                    current_preferences=current_preferences,
                    credential_service=credential_service,
                )
                preferences = UserTextPreferences(
                    user_id=current_user.user_id,
                    provider=request_body.provider,
                    model=request_body.model,
                    credential_ref=None if credential is None else credential.id,
                    provider_config=current_preferences.provider_config.model_copy(
                        update={
                            "agent_instructions": request_body.agent_instructions,
                            "preferred_language": request_body.preferred_language,
                            "temperature": request_body.temperature,
                            "allowed_subagent_models": allowed_subagent_models,
                            "subagent_model": subagent_model,
                        },
                    ),
                )
                await preference_service.set_preferences(preferences)
                summaries = await credential_service.list_active_credential_summaries(
                    current_user.user_id,
                )
        except MissingTextCredentialError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=missing_text_key_detail(request_body.provider),
            ) from exc
        except SQLAlchemyError as exc:
            raise unavailable() from exc

        return settings_response(
            preferences,
            credential,
            credential_summaries=summaries,
        )

    @router.put(
        "/settings/openai-compatible",
        response_model=TextProviderSettingsResponse,
    )
    async def put_openai_compatible_settings(
        request_body: OpenAICompatibleProviderRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> TextProviderSettingsResponse:
        try:
            endpoint = await resolved_endpoint_policy.resolve_url(
                request_body.base_url,
                OutboundEndpointPurpose.TEXT_PROVIDER,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OpenAI-compatible endpoint URL is not permitted",
            ) from exc
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                preference_service = UserTextPreferenceService(session)
                credential_service = UserTextCredentialService(
                    session=session,
                    secret_vault=secret_vault,
                )
                current_preferences = await existing_preferences(
                    user_id=current_user.user_id,
                    preference_service=preference_service,
                )
                credential = None
                if request_body.api_key is not None:
                    credential = await credential_service.create_credential(
                        user_id=current_user.user_id,
                        provider=TextProvider.OPENAI_COMPATIBLE,
                        label=text_provider_definition(TextProvider.OPENAI_COMPATIBLE).label,
                        secret=request_body.api_key,
                    )
                credential_ref = None if credential is None else credential.id
                config_updates: dict[str, object] = {
                    "base_url": endpoint.url,
                    "available_models": request_body.available_models,
                    "openai_compatible_credential_ref": credential_ref,
                }
                preference_updates: dict[str, object] = {}
                if current_preferences.provider == TextProvider.OPENAI_COMPATIBLE:
                    selected_model = (
                        current_preferences.model
                        if current_preferences.model in request_body.available_models
                        else request_body.available_models[0]
                    )
                    allowed_models = (
                        tuple(
                            model
                            for model in current_preferences.provider_config.allowed_subagent_models
                            if model in request_body.available_models
                        )
                        or request_body.available_models
                    )
                    subagent_model = current_preferences.provider_config.subagent_model
                    if subagent_model not in allowed_models:
                        subagent_model = allowed_models[0]
                    config_updates.update(
                        {
                            "allowed_subagent_models": allowed_models,
                            "subagent_model": subagent_model,
                        },
                    )
                    preference_updates.update(
                        {
                            "model": selected_model,
                            "credential_ref": credential_ref,
                        },
                    )
                provider_config = current_preferences.provider_config.model_copy(
                    update=config_updates,
                )
                preferences = current_preferences.model_copy(
                    update={
                        **preference_updates,
                        "provider_config": provider_config,
                    },
                )
                await preference_service.set_preferences(preferences)
                active_credential = await active_credential_for_preferences(
                    user_id=current_user.user_id,
                    preferences=preferences,
                    credential_service=credential_service,
                )
                summaries = await credential_service.list_active_credential_summaries(
                    current_user.user_id,
                )
        except SQLAlchemyError as exc:
            raise unavailable() from exc

        return provider_settings_response(
            preferences=preferences,
            active_credential=active_credential,
            credential_summaries=summaries,
            image_input_enabled=image_input_enabled,
        )

    @router.put(
        "/settings/image-generation",
        response_model=TextSettingsResponse,
    )
    async def put_image_generation_settings(
        request_body: ImageGenerationPreferencesRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> TextSettingsResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                preference_service = UserTextPreferenceService(session)
                credential_service = UserTextCredentialService(
                    session=session,
                    secret_vault=secret_vault,
                )
                current_preferences = await existing_preferences(
                    user_id=current_user.user_id,
                    preference_service=preference_service,
                )
                await credential_service.get_latest_active_credential_record(
                    user_id=current_user.user_id,
                    provider=request_body.provider,
                )
                provider_config = current_preferences.provider_config.model_copy(
                    update={
                        "image_generation_provider": request_body.provider,
                        "image_generation_model": request_body.model,
                    },
                )
                preferences = current_preferences.model_copy(
                    update={"provider_config": provider_config},
                )
                await preference_service.set_preferences(preferences)
                active_credential = await active_credential_for_preferences(
                    user_id=current_user.user_id,
                    preferences=preferences,
                    credential_service=credential_service,
                )
                summaries = await credential_service.list_active_credential_summaries(
                    current_user.user_id,
                )
        except MissingTextCredentialError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Save a {text_provider_definition(request_body.provider).label} "
                    "key before saving image generation preferences"
                ),
            ) from exc
        except SQLAlchemyError as exc:
            raise unavailable() from exc

        return settings_response(
            preferences,
            active_credential,
            credential_summaries=summaries,
        )

    @router.put(
        "/settings/provider-credential",
        response_model=TextProviderSettingsResponse,
    )
    async def put_provider_credential(
        request_body: TextProviderCredentialRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> TextProviderSettingsResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                preference_service = UserTextPreferenceService(session)
                credential_service = UserTextCredentialService(
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
                summaries = await credential_service.list_active_credential_summaries(
                    current_user.user_id,
                )
        except SQLAlchemyError as exc:
            raise unavailable() from exc

        return provider_settings_response(
            preferences=preferences,
            active_credential=credential,
            credential_summaries=summaries,
            image_input_enabled=image_input_enabled,
        )

    @router.put("/settings/active-provider", response_model=TextProviderSettingsResponse)
    async def put_active_provider(
        request_body: ActiveTextProviderRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> TextProviderSettingsResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                preference_service = UserTextPreferenceService(session)
                credential_service = UserTextCredentialService(
                    session=session,
                    secret_vault=secret_vault,
                )
                current_preferences = await existing_preferences(
                    user_id=current_user.user_id,
                    preference_service=preference_service,
                )
                credential = await preferred_credential_for_provider(
                    user_id=current_user.user_id,
                    provider=request_body.provider,
                    current_preferences=current_preferences,
                    credential_service=credential_service,
                )
                if request_body.provider == TextProvider.OPENAI_COMPATIBLE:
                    if not openai_compatible_is_configured(current_preferences):
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail="Configure the OpenAI-compatible endpoint before activating it",
                        )
                    models = current_preferences.provider_config.available_models
                    provider_config = current_preferences.provider_config.model_copy(
                        update={
                            "allowed_subagent_models": models,
                            "subagent_model": models[0],
                        },
                    )
                    preferences = UserTextPreferences(
                        user_id=current_user.user_id,
                        provider=request_body.provider,
                        model=models[0],
                        credential_ref=None if credential is None else credential.id,
                        provider_config=provider_config,
                    )
                else:
                    if credential is None:
                        raise MissingTextCredentialError(request_body.provider.value)
                    preferences = preferences_for_credential(
                        user_id=current_user.user_id,
                        provider=request_body.provider,
                        credential_ref=credential.id,
                        current_preferences=current_preferences,
                    )
                await preference_service.set_preferences(preferences)
                summaries = await credential_service.list_active_credential_summaries(
                    current_user.user_id,
                )
        except MissingTextCredentialError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=missing_text_key_detail(request_body.provider),
            ) from exc
        except SQLAlchemyError as exc:
            raise unavailable() from exc

        return provider_settings_response(
            preferences=preferences,
            active_credential=credential,
            credential_summaries=summaries,
            image_input_enabled=image_input_enabled,
        )

    return router


async def existing_preferences(
    user_id: str,
    preference_service: UserTextPreferenceService,
) -> UserTextPreferences:
    try:
        return await preference_service.get_preferences(user_id)
    except TextPreferencesNotFoundError:
        return default_text_preferences(user_id)


async def active_credential_for_preferences(
    user_id: str,
    preferences: UserTextPreferences,
    credential_service: UserTextCredentialService,
) -> UserProviderCredentialRecord | None:
    if preferences.credential_ref is None:
        return None
    try:
        return await credential_service.get_active_credential_record(
            user_id=user_id,
            provider=preferences.provider,
            credential_ref=preferences.credential_ref,
        )
    except MissingTextCredentialError:
        return None


async def preferred_credential_for_provider(
    user_id: str,
    provider: TextProvider,
    current_preferences: UserTextPreferences,
    credential_service: UserTextCredentialService,
) -> UserProviderCredentialRecord | None:
    if provider == TextProvider.OPENAI_COMPATIBLE:
        credential_ref = current_preferences.provider_config.openai_compatible_credential_ref
        if credential_ref is None:
            return None
        return await credential_service.get_active_credential_record(
            user_id=user_id,
            provider=provider,
            credential_ref=credential_ref,
        )
    if current_preferences.provider == provider and current_preferences.credential_ref:
        try:
            return await credential_service.get_active_credential_record(
                user_id=user_id,
                provider=provider,
                credential_ref=current_preferences.credential_ref,
            )
        except MissingTextCredentialError:
            pass
    return await credential_service.get_latest_active_credential_record(
        user_id=user_id,
        provider=provider,
    )


async def save_provider_credential(
    *,
    user_id: str,
    provider: TextProvider,
    secret: SecretStr,
    activate: bool,
    preference_service: UserTextPreferenceService,
    credential_service: UserTextCredentialService,
) -> tuple[UserTextPreferences, UserProviderCredentialRecord | None]:
    current_preferences = await existing_preferences(
        user_id=user_id,
        preference_service=preference_service,
    )
    credential = await credential_service.create_credential(
        user_id=user_id,
        provider=provider,
        label=text_provider_definition(provider).label,
        secret=secret,
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

    if current_preferences.provider == provider:
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
    *,
    user_id: str,
    provider: TextProvider,
    credential_ref: str,
    current_preferences: UserTextPreferences,
) -> UserTextPreferences:
    if current_preferences.provider == provider:
        return current_preferences.model_copy(
            update={"credential_ref": credential_ref},
        )
    definition = text_provider_definition(provider)
    provider_config = default_text_provider_config(provider).model_copy(
        update={
            "base_url": current_preferences.provider_config.base_url,
            "available_models": current_preferences.provider_config.available_models,
            "openai_compatible_credential_ref": (current_preferences.provider_config.openai_compatible_credential_ref),
            "image_generation_provider": (current_preferences.provider_config.image_generation_provider),
            "image_generation_model": (current_preferences.provider_config.image_generation_model),
        },
    )
    return UserTextPreferences(
        user_id=user_id,
        provider=provider,
        model=definition.default_model,
        credential_ref=credential_ref,
        provider_config=provider_config,
    )


def provider_settings_response(
    *,
    preferences: UserTextPreferences,
    active_credential: UserProviderCredentialRecord | None,
    credential_summaries: tuple[TextCredentialSummary, ...],
    image_input_enabled: bool = True,
) -> TextProviderSettingsResponse:
    summaries_by_provider: dict[TextProvider, TextCredentialSummary] = {}
    for summary in credential_summaries:
        summaries_by_provider.setdefault(summary.provider, summary)
    return TextProviderSettingsResponse(
        active_provider=preferences.provider,
        active_settings=settings_response(
            preferences,
            active_credential,
            credential_summaries=credential_summaries,
        ),
        image_input_enabled=image_input_enabled,
        providers=tuple(
            provider_summary_response(
                provider=provider,
                active_provider=preferences.provider,
                summary=summaries_by_provider.get(provider),
                preferences=preferences,
            )
            for provider in TextProvider
        ),
    )


def settings_response(
    preferences: UserTextPreferences,
    credential: UserProviderCredentialRecord | None,
    *,
    credential_summaries: tuple[TextCredentialSummary, ...],
) -> TextSettingsResponse:
    configured_providers = frozenset(summary.provider for summary in credential_summaries)
    image_choice = displayed_image_generation_model(
        preferences=preferences,
        configured_providers=configured_providers,
    )
    return TextSettingsResponse(
        configured=(
            openai_compatible_is_configured(preferences)
            if preferences.provider == TextProvider.OPENAI_COMPATIBLE
            else credential is not None
        ),
        provider=preferences.provider,
        model=effective_text_model(preferences),
        preferred_language=preferences.provider_config.preferred_language,
        agent_instructions=preferences.provider_config.agent_instructions,
        temperature=preferences.provider_config.temperature,
        allowed_subagent_models=effective_allowed_subagent_models(preferences),
        subagent_model=effective_subagent_model(preferences),
        image_generation_provider=image_choice.provider,
        image_generation_model=image_choice.model,
    )


def provider_summary_response(
    *,
    provider: TextProvider,
    active_provider: TextProvider,
    summary: TextCredentialSummary | None,
    preferences: UserTextPreferences,
) -> TextProviderSummaryResponse:
    definition = text_provider_definition(provider)
    model_options = definition.model_options
    default_model = definition.default_model
    default_subagent_model = definition.default_subagent_model
    configured = summary is not None
    credential_last_four = None if summary is None else summary.last_four
    base_url = None
    if provider == TextProvider.OPENAI_COMPATIBLE:
        configured_models = preferences.provider_config.available_models
        model_options = tuple(
            ProviderModelOption(
                model=model,
                label=model,
                description=(
                    "User-configured OpenAI-compatible model. Voxos requires "
                    "streaming chat completions and structured tool calling."
                ),
                supports_tool_calling=True,
            )
            for model in configured_models
        )
        if configured_models:
            default_model = configured_models[0]
            default_subagent_model = configured_models[0]
        configured = openai_compatible_is_configured(preferences)
        base_url = preferences.provider_config.base_url
        expected_credential_ref = preferences.provider_config.openai_compatible_credential_ref
        if summary is None or summary.credential_ref != expected_credential_ref:
            credential_last_four = None
    return TextProviderSummaryResponse(
        provider=provider,
        label=definition.label,
        api_key_label=definition.api_key_label,
        configured=configured,
        active=provider == active_provider,
        runtime_supported=definition.runtime_supported,
        default_model=default_model,
        default_multimodal_model=definition.default_multimodal_model,
        default_browser_model=definition.default_browser_model,
        suggested_models=tuple(option.model for option in model_options),
        model_options=model_options,
        default_image_generation_model=definition.default_image_generation_model,
        image_generation_model_options=definition.image_generation_model_options,
        default_subagent_model=default_subagent_model,
        allowed_subagent_models=tuple(option.model for option in model_options),
        credential_last_four=credential_last_four,
        base_url=base_url,
        docs_url=definition.docs_url,
        logo=definition.logo,
    )


def missing_text_key_detail(provider: TextProvider) -> str:
    return f"Save a {text_provider_definition(provider).label} key before saving text preferences"


def openai_compatible_is_configured(preferences: UserTextPreferences) -> bool:
    config = preferences.provider_config
    return config.base_url is not None and bool(config.available_models)


def validated_text_model_selection(
    request: TextPreferencesRequest,
    current_preferences: UserTextPreferences,
) -> tuple[tuple[str, ...], str]:
    if request.provider == TextProvider.OPENAI_COMPATIBLE:
        supported_models = set(current_preferences.provider_config.available_models)
        if current_preferences.provider_config.base_url is None or not supported_models:
            message = "Configure the OpenAI-compatible endpoint before selecting it"
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=message,
            )
        allowed_models = request.allowed_subagent_models or current_preferences.provider_config.available_models
        subagent_model = request.subagent_model or allowed_models[0]
    else:
        supported_models = set(text_provider_definition(request.provider).available_models)
        allowed_models = request.effective_allowed_subagent_models()
        subagent_model = request.effective_subagent_model()

    if request.model not in supported_models:
        message = f"Model must be configured for {request.provider.value}: {request.model}"
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=message,
        )
    unknown_models = set(allowed_models) - supported_models
    if unknown_models:
        message = (
            f"Allowed subagent models must be configured for the selected provider: {', '.join(sorted(unknown_models))}"
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=message,
        )
    if subagent_model not in allowed_models:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Default subagent model must be in the allowed subagent model list",
        )
    return allowed_models, subagent_model


def unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Text settings are unavailable",
    )

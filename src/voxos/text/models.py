from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from voxos.logos.domains import logo_for_text_provider
from voxos.logos.models import BrandLogo
from voxos.providers.model_catalog import (
    NVIDIA_IMAGE_GENERATION_MODEL_OPTIONS,
    NVIDIA_TEXT_MODEL_OPTIONS,
    OPENAI_IMAGE_GENERATION_MODEL_OPTIONS,
    OPENAI_TEXT_MODEL_OPTIONS,
    XAI_IMAGE_GENERATION_MODEL_OPTIONS,
    XAI_TEXT_MODEL_OPTIONS,
    ProviderImageGenerationModelOption,
    ProviderModelOption,
    image_generation_model_ids,
    model_ids,
)

DEFAULT_TEXT_MODEL = "gpt-5.4-mini"
DEFAULT_TEXT_AGENT_INSTRUCTIONS = "You are a helpful assistant."
DEFAULT_TEXT_LANGUAGE = "English"


class TextProvider(StrEnum):
    OPENAI = "openai"
    OPENAI_COMPATIBLE = "openai_compatible"
    XAI = "xai"
    NVIDIA = "nvidia"


class TextProviderProtocol(StrEnum):
    RESPONSES = "responses"
    CHAT_COMPLETIONS = "chat_completions"


class TextReasoningMode(StrEnum):
    """Select whether a provider should spend tokens on internal reasoning."""

    FAST = "fast"
    AGENTIC = "agentic"


class TextProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_instructions: str = Field(
        default=DEFAULT_TEXT_AGENT_INSTRUCTIONS,
        min_length=1,
    )
    preferred_language: str = Field(
        default=DEFAULT_TEXT_LANGUAGE,
        min_length=1,
        max_length=128,
    )
    base_url: str | None = Field(default=None, min_length=1)
    available_models: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    openai_compatible_credential_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=36,
    )
    temperature: float | None = Field(default=None, ge=0, le=2)
    allowed_subagent_models: tuple[str, ...] = Field(default_factory=tuple)
    subagent_model: str | None = Field(default=None, min_length=1, max_length=256)
    image_generation_provider: TextProvider | None = None
    image_generation_model: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator(
        "agent_instructions",
        "preferred_language",
        "base_url",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("allowed_subagent_models", "available_models", mode="before")
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

    @field_validator("subagent_model", "image_generation_model", mode="before")
    @classmethod
    def strip_optional_model(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def validate_subagent_model(self) -> "TextProviderConfig":
        if (
            self.subagent_model is not None
            and self.allowed_subagent_models
            and self.subagent_model not in self.allowed_subagent_models
        ):
            message = "Default subagent model must be in the allowed subagent model list"
            raise ValueError(message)
        if (self.image_generation_provider is None) != (self.image_generation_model is None):
            message = "Image generation provider and model must be configured together"
            raise ValueError(message)
        return self


class UserTextPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=256)
    provider: TextProvider = TextProvider.OPENAI
    model: str = Field(default=DEFAULT_TEXT_MODEL, min_length=1, max_length=256)
    credential_ref: str | None = Field(default=None, min_length=1, max_length=36)
    provider_config: TextProviderConfig = Field(default_factory=TextProviderConfig)

    @model_validator(mode="after")
    def validate_provider_configuration(self) -> "UserTextPreferences":
        validate_text_provider_temperature(
            self.provider,
            self.provider_config.temperature,
        )
        return self


class ResolvedTextCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: TextProvider
    reference: str = Field(min_length=1, max_length=36)
    token: SecretStr


class TextProviderDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: TextProvider
    label: str = Field(min_length=1)
    api_key_label: str = Field(min_length=1)
    default_model: str = Field(min_length=1)
    default_multimodal_model: str | None = Field(default=None, min_length=1)
    default_browser_model: str | None = Field(default=None, min_length=1)
    model_options: tuple[ProviderModelOption, ...] = Field(default_factory=tuple)
    default_image_generation_model: str | None = Field(default=None, min_length=1)
    image_generation_model_options: tuple[
        ProviderImageGenerationModelOption,
        ...,
    ] = Field(default_factory=tuple)
    default_subagent_model: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    protocol: TextProviderProtocol
    max_temperature: float = Field(default=2, ge=0, le=2)
    allow_custom_base_url: bool = True
    allow_user_models: bool = False
    docs_url: str = Field(min_length=1)
    runtime_supported: bool = True
    compatible_credential_providers: tuple[str, ...] = Field(default_factory=tuple)
    logo: BrandLogo

    @property
    def available_models(self) -> tuple[str, ...]:
        return model_ids(self.model_options)

    @property
    def available_image_generation_models(self) -> tuple[str, ...]:
        return image_generation_model_ids(self.image_generation_model_options)

    @model_validator(mode="after")
    def validate_model_policy(self) -> "TextProviderDefinition":
        options = {option.model: option for option in self.model_options}
        if not self.allow_user_models and self.default_model not in options:
            message = "Default model must be in the provider model catalog"
            raise ValueError(message)
        if not self.allow_user_models and self.default_subagent_model not in options:
            message = "Default subagent model must be in the provider model catalog"
            raise ValueError(message)
        if (
            self.default_image_generation_model is not None
            and self.default_image_generation_model not in self.available_image_generation_models
        ):
            message = "Default image generation model must be in the provider image model catalog"
            raise ValueError(message)
        if not self.allow_user_models and not options[self.default_model].supports_tool_calling:
            message = "Default text model must support tool calling"
            raise ValueError(message)
        if self.default_multimodal_model is not None:
            multimodal = options.get(self.default_multimodal_model)
            if multimodal is None or not multimodal.supports_image_input:
                message = "Default multimodal model must support image input"
                raise ValueError(message)
        if self.default_browser_model is not None:
            browser = options.get(self.default_browser_model)
            if browser is None or not browser.supports_browser_use:
                message = "Default browser model must support browser use"
                raise ValueError(message)
        return self


class TextChatSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    agent_id: str = Field(min_length=1, max_length=36)
    title: str | None = Field(default=None, min_length=1, max_length=256)
    provider: TextProvider
    model: str = Field(min_length=1, max_length=256)
    supports_image_input: bool = False
    status: str = Field(min_length=1, max_length=32)
    started_at: datetime
    updated_at: datetime


TEXT_PROVIDER_DEFINITIONS: dict[TextProvider, TextProviderDefinition] = {
    TextProvider.OPENAI: TextProviderDefinition(
        provider=TextProvider.OPENAI,
        label="OpenAI",
        api_key_label="OpenAI API key",  # pragma: allowlist secret
        default_model=DEFAULT_TEXT_MODEL,
        default_multimodal_model=DEFAULT_TEXT_MODEL,
        default_browser_model=DEFAULT_TEXT_MODEL,
        model_options=OPENAI_TEXT_MODEL_OPTIONS,
        default_image_generation_model="gpt-image-2",
        image_generation_model_options=OPENAI_IMAGE_GENERATION_MODEL_OPTIONS,
        default_subagent_model="gpt-5.6-terra",
        base_url="https://api.openai.com/v1",
        protocol=TextProviderProtocol.RESPONSES,
        docs_url="https://developers.openai.com/api/docs/models",
        allow_custom_base_url=False,
        compatible_credential_providers=("openai_realtime",),
        logo=logo_for_text_provider(TextProvider.OPENAI.value),
    ),
    TextProvider.OPENAI_COMPATIBLE: TextProviderDefinition(
        provider=TextProvider.OPENAI_COMPATIBLE,
        label="OpenAI compatible",
        api_key_label="API key (optional)",  # pragma: allowlist secret
        default_model="local-model",
        model_options=(),
        default_image_generation_model=None,
        image_generation_model_options=(),
        default_subagent_model="local-model",
        base_url="http://localhost:11434/v1",
        protocol=TextProviderProtocol.CHAT_COMPLETIONS,
        allow_user_models=True,
        docs_url="https://docs.ollama.com/api/openai-compatibility",
        logo=logo_for_text_provider(TextProvider.OPENAI_COMPATIBLE.value),
    ),
    TextProvider.XAI: TextProviderDefinition(
        provider=TextProvider.XAI,
        label="xAI",
        api_key_label="xAI API key",  # pragma: allowlist secret
        default_model="grok-4.5",
        default_multimodal_model="grok-4.5",
        default_browser_model="grok-4.5",
        model_options=XAI_TEXT_MODEL_OPTIONS,
        default_image_generation_model="grok-imagine-image-quality",
        image_generation_model_options=XAI_IMAGE_GENERATION_MODEL_OPTIONS,
        default_subagent_model="grok-4.3",
        base_url="https://api.x.ai/v1",
        protocol=TextProviderProtocol.RESPONSES,
        docs_url="https://docs.x.ai/developers/models",
        allow_custom_base_url=False,
        compatible_credential_providers=("grok_voice_agent",),
        logo=logo_for_text_provider(TextProvider.XAI.value),
    ),
    TextProvider.NVIDIA: TextProviderDefinition(
        provider=TextProvider.NVIDIA,
        label="NVIDIA API Catalog",
        api_key_label="NVIDIA API key",  # pragma: allowlist secret
        default_model="openai/gpt-oss-20b",
        default_multimodal_model="stepfun-ai/step-3.7-flash",
        default_browser_model="stepfun-ai/step-3.7-flash",
        model_options=NVIDIA_TEXT_MODEL_OPTIONS,
        default_image_generation_model="black-forest-labs/flux.2-klein-4b",
        image_generation_model_options=NVIDIA_IMAGE_GENERATION_MODEL_OPTIONS,
        default_subagent_model="openai/gpt-oss-20b",
        base_url="https://integrate.api.nvidia.com/v1",
        protocol=TextProviderProtocol.CHAT_COMPLETIONS,
        max_temperature=1,
        allow_custom_base_url=False,
        docs_url="https://docs.api.nvidia.com/nim/re/reference/llm-apis",
        logo=logo_for_text_provider(TextProvider.NVIDIA.value),
    ),
}


def text_provider_definition(provider: TextProvider) -> TextProviderDefinition:
    return TEXT_PROVIDER_DEFINITIONS[provider]


def text_model_supports_image_input(provider: TextProvider, model: str) -> bool:
    option = text_model_option(provider, model)
    return option is not None and option.supports_image_input


def text_model_supports_tool_calling(provider: TextProvider, model: str) -> bool:
    option = text_model_option(provider, model)
    return option is not None and option.supports_tool_calling


def text_model_supports_browser_use(provider: TextProvider, model: str) -> bool:
    option = text_model_option(provider, model)
    return option is not None and option.supports_browser_use


def text_model_for_image_input(
    provider: TextProvider,
    preferred_model: str,
) -> str | None:
    option = text_model_option(provider, preferred_model)
    if option is None:
        return None
    if option.supports_image_input:
        return option.model
    return text_provider_definition(provider).default_multimodal_model


def text_model_for_browser_use(
    provider: TextProvider,
    preferred_model: str,
) -> str | None:
    option = text_model_option(provider, preferred_model)
    if option is not None and option.supports_browser_use:
        return option.model
    return text_provider_definition(provider).default_browser_model


def text_model_option(
    provider: TextProvider,
    model: str,
) -> ProviderModelOption | None:
    return next(
        (option for option in text_provider_definition(provider).model_options if option.model == model),
        None,
    )


def image_generation_model_option(
    provider: TextProvider,
    model: str,
) -> ProviderImageGenerationModelOption | None:
    return next(
        (
            option
            for option in text_provider_definition(provider).image_generation_model_options
            if option.model == model
        ),
        None,
    )


def validate_text_provider_temperature(
    provider: TextProvider,
    temperature: float | None,
) -> None:
    if temperature is None:
        return
    maximum = text_provider_definition(provider).max_temperature
    if temperature > maximum:
        message = f"Temperature must be between 0 and {maximum:g} for {provider.value}"
        raise ValueError(message)


def default_text_provider_config(provider: TextProvider) -> TextProviderConfig:
    definition = text_provider_definition(provider)
    return TextProviderConfig(
        allowed_subagent_models=definition.available_models,
        subagent_model=definition.default_subagent_model,
    )


def effective_text_model(preferences: UserTextPreferences) -> str:
    definition = text_provider_definition(preferences.provider)
    available_models = available_text_models(preferences)
    if preferences.model in available_models:
        return preferences.model
    if definition.allow_user_models and available_models:
        return available_models[0]
    return definition.default_model


def effective_image_generation_provider(
    preferences: UserTextPreferences,
) -> TextProvider:
    configured = preferences.provider_config.image_generation_provider
    if configured is not None:
        return configured
    if text_provider_definition(preferences.provider).default_image_generation_model is None:
        return TextProvider.OPENAI
    return preferences.provider


def effective_image_generation_model(preferences: UserTextPreferences) -> str:
    provider = effective_image_generation_provider(preferences)
    definition = text_provider_definition(provider)
    configured = preferences.provider_config.image_generation_model
    if configured in definition.available_image_generation_models:
        return configured
    if definition.default_image_generation_model is not None:
        return definition.default_image_generation_model
    return text_provider_definition(TextProvider.OPENAI).default_image_generation_model or "gpt-image-2"


def effective_allowed_subagent_models(
    preferences: UserTextPreferences,
) -> tuple[str, ...]:
    available_models = available_text_models(preferences)
    configured = preferences.provider_config.allowed_subagent_models
    if configured:
        configured_models = tuple(model for model in configured if model in available_models)
        if configured_models:
            return configured_models
    return available_models


def effective_subagent_model(preferences: UserTextPreferences) -> str:
    allowed_models = effective_allowed_subagent_models(preferences)
    configured = preferences.provider_config.subagent_model
    if configured is not None and configured in allowed_models:
        return configured
    definition = text_provider_definition(preferences.provider)
    if definition.default_subagent_model in allowed_models:
        return definition.default_subagent_model
    if preferences.model in allowed_models:
        return preferences.model
    if allowed_models:
        return allowed_models[0]
    return definition.default_subagent_model


def available_text_models(preferences: UserTextPreferences) -> tuple[str, ...]:
    definition = text_provider_definition(preferences.provider)
    if definition.allow_user_models and preferences.provider_config.available_models:
        return preferences.provider_config.available_models
    return definition.available_models

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from voxos.agent_runtime.subagents import SubAgentModelSelection
from voxos.db.session import AsyncSessionFactory
from voxos.text.errors import TextPreferencesNotFoundError
from voxos.text.models import (
    TextProvider,
    UserTextPreferences,
    default_text_provider_config,
    effective_allowed_subagent_models,
    effective_subagent_model,
    text_provider_definition,
)
from voxos.text.storage import UserTextPreferenceService


class SubAgentModelPolicySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: TextProvider
    allowed_models: tuple[str, ...] = Field(min_length=1)
    default_model: str = Field(min_length=1, max_length=256)


@dataclass(frozen=True, slots=True)
class TextSubAgentModelPolicy:
    session_factory: AsyncSessionFactory

    async def settings(
        self,
        *,
        user_id: str,
        provider: TextProvider,
        parent_model: str | None = None,
    ) -> SubAgentModelPolicySettings:
        preferences = await self._preferences(user_id=user_id, provider=provider)
        allowed_models = effective_allowed_subagent_models(preferences)
        default_model = effective_subagent_model(preferences)
        if parent_model is not None and parent_model in allowed_models:
            default_model = default_model if default_model in allowed_models else parent_model
        return SubAgentModelPolicySettings(
            provider=provider,
            allowed_models=allowed_models,
            default_model=default_model,
        )

    async def select_model(
        self,
        *,
        user_id: str,
        parent_provider: str | None,
        parent_model: str | None,
        requested_model: str | None,
    ) -> SubAgentModelSelection:
        provider = self._provider(parent_provider)
        settings = await self.settings(
            user_id=user_id,
            provider=provider,
            parent_model=parent_model,
        )
        selected_model = requested_model or settings.default_model
        if selected_model not in settings.allowed_models:
            message = f"Subagent model is not allowed for {provider.value}: {selected_model}"
            raise ValueError(message)
        return SubAgentModelSelection(
            provider=provider.value,
            model=selected_model,
        )

    async def _preferences(
        self,
        *,
        user_id: str,
        provider: TextProvider,
    ) -> UserTextPreferences:
        async with self.session_factory() as session:
            try:
                preferences = await UserTextPreferenceService(session).get_preferences(
                    user_id,
                )
            except TextPreferencesNotFoundError:
                preferences = None
        if preferences is not None and preferences.provider == provider:
            return preferences
        if (
            preferences is not None
            and provider == TextProvider.OPENAI_COMPATIBLE
            and preferences.provider_config.available_models
            and preferences.provider_config.base_url is not None
        ):
            models = preferences.provider_config.available_models
            provider_config = preferences.provider_config.model_copy(
                update={
                    "allowed_subagent_models": models,
                    "subagent_model": models[0],
                },
            )
            return UserTextPreferences(
                user_id=user_id,
                provider=provider,
                model=models[0],
                credential_ref=provider_config.openai_compatible_credential_ref,
                provider_config=provider_config,
            )
        definition = text_provider_definition(provider)
        return UserTextPreferences(
            user_id=user_id,
            provider=provider,
            model=definition.default_model,
            credential_ref=None,
            provider_config=default_text_provider_config(provider),
        )

    @staticmethod
    def _provider(parent_provider: str | None) -> TextProvider:
        if parent_provider is None:
            return TextProvider.OPENAI
        try:
            return TextProvider(parent_provider)
        except ValueError as exc:
            message = f"Unsupported subagent text provider: {parent_provider}"
            raise ValueError(message) from exc

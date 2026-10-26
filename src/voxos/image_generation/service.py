from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from voxos.agent_runtime.records import AgentTranscriptRecord
from voxos.attachments.models import AttachmentClientSource, AttachmentVariant
from voxos.attachments.records import MessageAttachmentRecord
from voxos.attachments.service import AttachmentService
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.image_generation.providers import (
    ImageGenerationInput,
    ImageGenerationProviderClient,
    ImageGenerationProviderRequest,
)
from voxos.image_generation.routing import (
    ImageGenerationChoice,
    choose_image_generation_model,
    displayed_image_generation_model,
)
from voxos.text.errors import TextPreferencesNotFoundError
from voxos.text.models import (
    ResolvedTextCredentials,
    TextProvider,
    UserTextPreferences,
    text_provider_definition,
)
from voxos.text.service import default_text_preferences
from voxos.text.storage import UserTextCredentialService, UserTextPreferenceService


class ImageGenerationUnavailableError(RuntimeError):
    """Raised when no enabled provider can satisfy an image request."""


class ImageGenerationAction(StrEnum):
    GENERATE = "generate"
    EDIT = "edit"


@dataclass(frozen=True, slots=True)
class ImageGenerationCapabilities:
    generation_available: bool
    editing_available: bool


@dataclass(frozen=True, slots=True)
class PersistedImageGeneration:
    provider: TextProvider
    model: str
    edited: bool
    user_message: AgentTranscriptRecord
    assistant_message: AgentTranscriptRecord
    attachment: MessageAttachmentRecord


@dataclass(frozen=True, slots=True)
class RenderedImageGeneration:
    provider: TextProvider
    model: str
    edited: bool
    content: bytes


@dataclass(frozen=True, slots=True)
class StagedImageGeneration:
    provider: TextProvider
    model: str
    edited: bool
    attachment: MessageAttachmentRecord


class ImageGenerationService:
    def __init__(
        self,
        *,
        session_factory: AsyncSessionFactory,
        secret_vault: UserSecretVaultService,
        attachment_service: AttachmentService,
        provider_client: ImageGenerationProviderClient,
    ) -> None:
        self._session_factory = session_factory
        self._secret_vault = secret_vault
        self._attachment_service = attachment_service
        self._provider_client = provider_client

    async def capabilities(self, *, user_id: str) -> ImageGenerationCapabilities:
        preferences, configured = await self._routing_context(user_id=user_id)
        return ImageGenerationCapabilities(
            generation_available=(
                choose_image_generation_model(
                    preferences=preferences,
                    configured_providers=configured,
                )
                is not None
            ),
            editing_available=(
                choose_image_generation_model(
                    preferences=preferences,
                    configured_providers=configured,
                    require_editing=True,
                )
                is not None
            ),
        )

    async def editing_available(self, user_id: str) -> bool:
        return (await self.capabilities(user_id=user_id)).editing_available

    async def generate(
        self,
        *,
        user_id: str,
        chat_id: str,
        agent_id: str,
        prompt: str,
        attachment_ids: tuple[str, ...],
        client_turn_id: str,
        client_source: AttachmentClientSource,
    ) -> PersistedImageGeneration:
        existing = await self._attachment_service.existing_generated_turn(
            user_id=user_id,
            chat_id=chat_id,
            client_turn_id=client_turn_id,
            prompt=prompt,
            source_attachment_ids=attachment_ids,
        )
        if existing is not None:
            user_message, assistant_message, attachment = existing
            existing_choice = _choice_from_assistant_text(assistant_message.text)
            if existing_choice is None:
                preferences, configured = await self._routing_context(user_id=user_id)
                existing_choice = displayed_image_generation_model(
                    preferences=preferences,
                    configured_providers=configured,
                )
            return PersistedImageGeneration(
                provider=existing_choice.provider,
                model=existing_choice.model,
                edited=bool(attachment_ids),
                user_message=user_message,
                assistant_message=assistant_message,
                attachment=attachment,
            )

        rendered = await self.render(
            user_id=user_id,
            chat_id=chat_id,
            prompt=prompt,
            attachment_ids=attachment_ids,
        )
        user_message, assistant_message, attachment = await self._attachment_service.persist_generated_turn(
            user_id=user_id,
            chat_id=chat_id,
            agent_id=agent_id,
            prompt=prompt,
            source_attachment_ids=attachment_ids,
            client_turn_id=client_turn_id,
            client_source=client_source,
            generated_content=rendered.content,
            assistant_text=_assistant_text(
                ImageGenerationChoice(
                    provider=rendered.provider,
                    model=rendered.model,
                ),
                edited=rendered.edited,
            ),
        )
        return PersistedImageGeneration(
            provider=rendered.provider,
            model=rendered.model,
            edited=rendered.edited,
            user_message=user_message,
            assistant_message=assistant_message,
            attachment=attachment,
        )

    async def generate_for_tool(
        self,
        *,
        user_id: str,
        chat_id: str,
        agent_id: str,
        run_id: str,
        call_id: str,
        prompt: str,
        action: ImageGenerationAction,
    ) -> StagedImageGeneration:
        client_draft_id = _tool_client_draft_id(run_id=run_id, call_id=call_id)
        existing = await self._attachment_service.existing_staged_generated_attachment(
            user_id=user_id,
            chat_id=chat_id,
            client_draft_id=client_draft_id,
        )
        attachment_ids: tuple[str, ...] = ()
        if action == ImageGenerationAction.EDIT:
            attachment_ids = await self._attachment_service.tool_edit_reference_ids(
                user_id=user_id,
                chat_id=chat_id,
                agent_id=agent_id,
                run_id=run_id,
            )
            if not attachment_ids:
                message = (
                    "There is no current or previously generated image to edit. "
                    "Ask the user to attach an image or generate one first."
                )
                raise ImageGenerationUnavailableError(message)
            if len(attachment_ids) > 3:
                message = (
                    "Image editing accepts at most three current reference images. "
                    "Ask the user to remove one and try again."
                )
                raise ImageGenerationUnavailableError(message)

        if existing is not None:
            preferences, configured = await self._routing_context(user_id=user_id)
            choice = choose_image_generation_model(
                preferences=preferences,
                configured_providers=configured,
                require_editing=action == ImageGenerationAction.EDIT,
            )
            if choice is None:
                raise ImageGenerationUnavailableError(
                    _unavailable_message(editing=action == ImageGenerationAction.EDIT),
                )
            return StagedImageGeneration(
                provider=choice.provider,
                model=choice.model,
                edited=action == ImageGenerationAction.EDIT,
                attachment=existing,
            )

        rendered = await self.render(
            user_id=user_id,
            chat_id=chat_id,
            prompt=prompt,
            attachment_ids=attachment_ids,
            require_ready=False,
        )
        attachment = await self._attachment_service.stage_generated_attachment(
            user_id=user_id,
            chat_id=chat_id,
            agent_id=agent_id,
            client_draft_id=client_draft_id,
            generated_content=rendered.content,
        )
        return StagedImageGeneration(
            provider=rendered.provider,
            model=rendered.model,
            edited=rendered.edited,
            attachment=attachment,
        )

    async def render(
        self,
        *,
        user_id: str,
        chat_id: str,
        prompt: str,
        attachment_ids: tuple[str, ...],
        require_ready: bool = True,
    ) -> RenderedImageGeneration:
        if require_ready:
            await self._attachment_service.validate_ready_set(
                user_id=user_id,
                chat_id=chat_id,
                attachment_ids=attachment_ids,
            )
        inputs: list[ImageGenerationInput] = []
        for attachment_id in attachment_ids:
            content, mime_type = await self._attachment_service.get_content(
                user_id=user_id,
                chat_id=chat_id,
                attachment_id=attachment_id,
                variant=AttachmentVariant.FULL,
            )
            inputs.append(ImageGenerationInput(content=content, mime_type=mime_type))

        preferences, configured = await self._routing_context(user_id=user_id)
        choice = choose_image_generation_model(
            preferences=preferences,
            configured_providers=configured,
            require_editing=bool(inputs),
        )
        if choice is None:
            raise ImageGenerationUnavailableError(
                _unavailable_message(editing=bool(inputs)),
            )
        credentials = await self._credentials(
            user_id=user_id,
            choice=choice,
        )
        generated = await self._provider_client.generate(
            ImageGenerationProviderRequest(
                provider=choice.provider,
                model=choice.model,
                prompt=prompt,
                inputs=tuple(inputs),
            ),
            credentials,
        )
        return RenderedImageGeneration(
            provider=choice.provider,
            model=choice.model,
            edited=bool(inputs),
            content=generated.content,
        )

    async def _routing_context(
        self,
        *,
        user_id: str,
    ) -> tuple[UserTextPreferences, frozenset[TextProvider]]:
        async with self._session_factory() as session:
            preference_service = UserTextPreferenceService(session)
            credential_service = UserTextCredentialService(
                session=session,
                secret_vault=self._secret_vault,
            )
            try:
                preferences = await preference_service.get_preferences(user_id)
            except TextPreferencesNotFoundError:
                preferences = default_text_preferences(user_id)
            summaries = await credential_service.list_active_credential_summaries(
                user_id,
            )
        return preferences, frozenset(summary.provider for summary in summaries)

    async def _credentials(
        self,
        *,
        user_id: str,
        choice: ImageGenerationChoice,
    ) -> ResolvedTextCredentials:
        async with self._session_factory() as session:
            service = UserTextCredentialService(
                session=session,
                secret_vault=self._secret_vault,
            )
            record = await service.get_latest_active_credential_record(
                user_id=user_id,
                provider=choice.provider,
            )
            return await service.resolve_credential(
                user_id=user_id,
                provider=choice.provider,
                credential_ref=record.id,
            )


def _unavailable_message(*, editing: bool) -> str:
    if editing:
        return (
            "Enable an OpenAI or xAI image provider to edit private images. "
            "The NVIDIA hosted preview currently supports prompt-only generation."
        )
    return "Save an NVIDIA, OpenAI, or xAI provider key before generating images"


def _assistant_text(
    choice: ImageGenerationChoice,
    *,
    edited: bool = False,
) -> str:
    definition = text_provider_definition(choice.provider)
    label = next(option.label for option in definition.image_generation_model_options if option.model == choice.model)
    action = "Edited" if edited else "Generated"
    return f"{action} with {label} via {definition.label}."


def _choice_from_assistant_text(text: str) -> ImageGenerationChoice | None:
    for provider in TextProvider:
        definition = text_provider_definition(provider)
        for option in definition.image_generation_model_options:
            choice = ImageGenerationChoice(provider=provider, model=option.model)
            if text in {
                _assistant_text(choice),
                _assistant_text(choice, edited=True),
            }:
                return choice
    return None


def _tool_client_draft_id(*, run_id: str, call_id: str) -> str:
    fingerprint = sha256(f"{run_id}:{call_id}".encode()).hexdigest()[:48]
    return f"agent-{fingerprint}"

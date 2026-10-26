import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated, TypeVar

import httpx
import orjson
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy.exc import SQLAlchemyError

from voxos.agent_core.browser_tools import BrowserAgentToolProvider
from voxos.agent_core.instructions import PersonaAgentInstructionProvider
from voxos.agent_core.integration_tools import IntegrationAgentToolProvider
from voxos.agent_core.internal_tools import InternalAgentToolProvider
from voxos.agent_core.mcp_tool_cache import McpToolCatalogCache
from voxos.agent_core.mcp_tools import CompositeAgentToolProvider, UserMcpAgentToolProvider
from voxos.agent_core.models import AgentEventKind, AgentToolProvider
from voxos.agent_core.subagent_tools import (
    DELEGATE_AGENT_TASK_TOOL_NAME,
    SubAgentToolProvider,
)
from voxos.agent_core.time_context import iso_datetime
from voxos.agent_identity.services import AgentIdentityNotFoundError, AgentIdentityService
from voxos.agent_memory.services import AgentMemoryService
from voxos.agent_planning.services import AgentPlanningService
from voxos.agent_runtime.effective_tool_policy import EffectiveToolPolicyService
from voxos.agent_runtime.history import TenantHistoryService
from voxos.agent_runtime.improvements import AgentImprovementService
from voxos.agent_runtime.learning_model import TextLearningModel
from voxos.agent_runtime.records import (
    AgentRunRecord,
    AgentRuntimeEventRecord,
    AgentSessionRecord,
    AgentToolEventRecord,
    AgentTranscriptRecord,
    AgentUsageRecord,
)
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.schedule_store import AgentScheduleStore
from voxos.agent_runtime.schedule_tools import AgentScheduleToolProvider
from voxos.agent_runtime.storage import SqlModelAgentRuntimeStore
from voxos.agent_runtime.subagents import SubAgentService
from voxos.agent_runtime.tool_authorization import SqlModelToolAuthorizationService
from voxos.agent_runtime.tool_routing import SqlModelToolRoutingService
from voxos.agent_skills.services import AgentSkillService
from voxos.attachments.models import (
    AttachmentClientSource,
    AttachmentSummary,
    AttachmentVariant,
)
from voxos.attachments.service import (
    AttachmentConflictError,
    AttachmentError,
    AttachmentModelUnsupportedError,
    AttachmentNotFoundError,
    AttachmentService,
    AttachmentValidationError,
    attachment_summary,
)
from voxos.attachments.settings import AttachmentSettings
from voxos.attachments.storage import (
    AttachmentObjectStore,
    attachment_object_store,
)
from voxos.auth.models import AuthenticatedUser
from voxos.browser_accounts.connection import McpBrowserCredentialResolver
from voxos.browser_accounts.model_resolution import BrowserTaskModelCredentialResolver
from voxos.browser_accounts.models import DEFAULT_BROWSER_COUNTRY_CODE
from voxos.browser_accounts.providers.base import BrowserAccountProviderAdapter
from voxos.browser_accounts.providers.browser_use_cloud import BrowserUseCloudProvider
from voxos.browser_accounts.providers.browserless import BrowserlessProvider
from voxos.browser_accounts.providers.composite import CompositeBrowserAccountProvider
from voxos.browser_accounts.providers.profileless import ProfilelessBrowserTaskExecutor
from voxos.browser_accounts.providers.stagehand_local import StagehandLocalProvider
from voxos.browser_accounts.secrets import BrowserAccountSecretStore
from voxos.browser_accounts.services import BrowserAccountService
from voxos.browser_accounts.settings import BrowserAccountSettings
from voxos.browser_runtime.service import BrowserRuntimeService
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory
from voxos.image_generation.providers import (
    HttpImageGenerationProviderClient,
    ImageGenerationProviderClient,
    ImageGenerationProviderError,
)
from voxos.image_generation.service import (
    ImageGenerationService,
    ImageGenerationUnavailableError,
)
from voxos.image_generation.tools import (
    GENERATE_IMAGE_TOOL_NAME,
    ImageGenerationAgentToolProvider,
)
from voxos.location.models import ClientLocation
from voxos.notifications.settings import PushNotificationSettings
from voxos.persona.rendering import ToolPolicyContext
from voxos.persona.services import AgentPersonaService
from voxos.place_cards.models import PlaceCardArtifact
from voxos.product_docs.retrieval import EnvironmentProductDocRetrievalService
from voxos.security.outbound_endpoints import OutboundEndpointPolicy
from voxos.text.errors import MissingTextCredentialError
from voxos.text.model_policy import TextSubAgentModelPolicy
from voxos.text.models import (
    TextChatSummary,
    TextProvider,
    text_model_for_image_input,
)
from voxos.text.providers import OpenAICompatibleTextProvider, TextProviderClient
from voxos.text.run_chat_service import RunChatService
from voxos.text.service import PreparedTextChatTurn, TextChatService
from voxos.text.telemetry import record_text_timing
from voxos.voice.call_routing import (
    LiveKitCallDemandCoordinator,
    LiveKitCallPreflight,
    LiveKitCallRouter,
)
from voxos.voice.livekit_tokens import LiveKitVoiceTokenService
from voxos.voice.mcp_tools import INTERNAL_AGENT_MCP_TOOL_NAMES
from voxos.voice.settings import VoiceAgentSettings

AuthenticatedUserDependency = Callable[[Request], Awaitable[AuthenticatedUser]]
AGENT_NOT_FOUND = "Agent was not found"
CHAT_NOT_FOUND = "Chat was not found"
DelegatedRecordT = TypeVar(
    "DelegatedRecordT",
    AgentTranscriptRecord,
    AgentRuntimeEventRecord,
    AgentToolEventRecord,
)


class ChatCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str | None = Field(default=None, min_length=1, max_length=36)
    title: str | None = Field(default=None, min_length=1, max_length=256)
    provider: TextProvider | None = None
    model: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("agent_id", "title", "model", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class ChatUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str | None = Field(default=None, min_length=1, max_length=256)
    provider: TextProvider | None = None
    model: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("title", "model", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class ChatMessageStreamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(default="", max_length=24000)
    attachment_ids: tuple[str, ...] = Field(
        default_factory=tuple,
        max_length=4,
    )
    client_turn_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
    )
    location: ClientLocation | None = None

    @field_validator("location", mode="before")
    @classmethod
    def discard_invalid_location(cls, value: object) -> object:
        if value is None or isinstance(value, ClientLocation):
            return value
        try:
            return ClientLocation.model_validate(value)
        except ValidationError:
            return None

    @field_validator("text", "client_turn_id", mode="before")
    @classmethod
    def strip_message(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def validate_content(self) -> "ChatMessageStreamRequest":
        if not self.text and not self.attachment_ids:
            message = "Message text or at least one image is required"
            raise ValueError(message)
        if self.attachment_ids and self.client_turn_id is None:
            message = "client_turn_id is required when sending images"
            raise ValueError(message)
        if len(set(self.attachment_ids)) != len(self.attachment_ids):
            message = "attachment_ids must be unique"
            raise ValueError(message)
        return self


class ChatImageGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt: str = Field(min_length=1, max_length=10000)
    attachment_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=3)
    client_turn_id: str = Field(min_length=1, max_length=58)
    client_source: AttachmentClientSource

    @field_validator("prompt", "client_turn_id", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def validate_attachments(self) -> "ChatImageGenerationRequest":
        if len(set(self.attachment_ids)) != len(self.attachment_ids):
            message = "attachment_ids must be unique"
            raise ValueError(message)
        return self


class ChatListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chats: tuple[TextChatSummary, ...] = Field(default_factory=tuple)
    image_input_enabled: bool
    image_generation_enabled: bool
    image_editing_enabled: bool


class ChatMessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    role: str
    text: str
    source: str
    interrupted: bool = False
    is_final: bool = True
    client_turn_id: str | None = None
    created_at: str
    attachments: tuple[AttachmentSummary, ...] = Field(default_factory=tuple)
    place_cards: tuple[PlaceCardArtifact, ...] = Field(default_factory=tuple)


class ChatImageGenerationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: TextProvider
    model: str = Field(min_length=1, max_length=256)
    edited: bool
    user_message: ChatMessageResponse
    assistant_message: ChatMessageResponse


class ChatRuntimeEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    kind: str
    payload_json: dict[str, object]
    created_at: str


class ChatToolEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    call_id: str
    name: str
    arguments_json: dict[str, object]
    output_text: str | None
    is_error: bool
    created_at: str


class ChatUsageEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    usage_type: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    raw_usage_json: dict[str, object]
    created_at: str


class ChatDelegatedRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    status: str
    task_prompt: str
    provider: str | None
    model: str | None
    chat_id: str | None
    output_text: str | None
    output_created_at: str | None
    runtime_events: tuple[ChatRuntimeEventResponse, ...] = Field(default_factory=tuple)
    tool_events: tuple[ChatToolEventResponse, ...] = Field(default_factory=tuple)
    created_at: str
    completed_at: str | None


class ChatDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chat: TextChatSummary
    messages: tuple[ChatMessageResponse, ...] = Field(default_factory=tuple)
    runtime_events: tuple[ChatRuntimeEventResponse, ...] = Field(default_factory=tuple)
    tool_events: tuple[ChatToolEventResponse, ...] = Field(default_factory=tuple)
    usage_events: tuple[ChatUsageEventResponse, ...] = Field(default_factory=tuple)
    delegated_runs: tuple[ChatDelegatedRunResponse, ...] = Field(default_factory=tuple)


class ChatDelegatedRunListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    delegated_runs: tuple[ChatDelegatedRunResponse, ...] = Field(default_factory=tuple)


def create_chats_router(
    auth_dependency: AuthenticatedUserDependency,
    session_factory: AsyncSessionFactory,
    secret_vault: UserSecretVaultService,
    http_client: httpx.AsyncClient,
    public_base_url: str | None = None,
    voice_settings: VoiceAgentSettings | None = None,
    text_provider_client: TextProviderClient | None = None,
    chat_service: TextChatService | None = None,
    attachment_settings: AttachmentSettings | None = None,
    attachment_store: AttachmentObjectStore | None = None,
    attachment_service: AttachmentService | None = None,
    image_generation_client: ImageGenerationProviderClient | None = None,
    browser_account_settings: BrowserAccountSettings | None = None,
    browser_account_provider: BrowserAccountProviderAdapter | None = None,
    push_settings: PushNotificationSettings | None = None,
    outbound_endpoint_policy: OutboundEndpointPolicy | None = None,
    mcp_tool_catalog_cache: McpToolCatalogCache | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/chats", tags=["chats"])
    resolved_attachment_settings = attachment_settings or AttachmentSettings()
    resolved_attachment_store = attachment_store or attachment_object_store(
        resolved_attachment_settings,
    )
    resolved_push_settings = push_settings or PushNotificationSettings()

    async def authenticated_user(request: Request) -> AuthenticatedUser:
        return await auth_dependency(request)

    def attachments_for(user_id: str) -> AttachmentService:
        if attachment_service is not None:
            return attachment_service
        if chat_service is not None and chat_service.attachment_service is not None:
            return chat_service.attachment_service
        return AttachmentService(
            session_factory=tenant_scoped_session_factory(
                session_factory=session_factory,
                user_id=user_id,
            ),
            object_store=resolved_attachment_store,
            settings=resolved_attachment_settings,
            chat_notifications_enabled=resolved_push_settings.enabled,
            chat_notification_max_attempts=resolved_push_settings.max_attempts,
        )

    def service_for(user_id: str) -> TextChatService:
        if chat_service is not None:
            return chat_service
        return build_text_chat_service(
            session_factory=tenant_scoped_session_factory(
                session_factory=session_factory,
                user_id=user_id,
            ),
            secret_vault=secret_vault,
            http_client=http_client,
            public_base_url=public_base_url,
            voice_settings=voice_settings,
            text_provider_client=text_provider_client,
            attachment_service=attachments_for(user_id),
            image_generation_client=image_generation_client,
            browser_account_settings=browser_account_settings,
            browser_account_provider=browser_account_provider,
            push_settings=resolved_push_settings,
            outbound_endpoint_policy=outbound_endpoint_policy,
            mcp_tool_catalog_cache=mcp_tool_catalog_cache,
        )

    def image_service_for(user_id: str) -> ImageGenerationService:
        return ImageGenerationService(
            session_factory=tenant_scoped_session_factory(
                session_factory=session_factory,
                user_id=user_id,
            ),
            secret_vault=secret_vault,
            attachment_service=attachments_for(user_id),
            provider_client=(image_generation_client or HttpImageGenerationProviderClient(http_client)),
        )

    @router.get("", response_model=ChatListResponse)
    async def list_chats(
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
        agent_id: str | None = None,
    ) -> ChatListResponse:
        service = service_for(current_user.user_id)
        try:
            chats = await service.list_chats(
                user_id=current_user.user_id,
                agent_id=agent_id,
            )
            image_capabilities = await image_service_for(
                current_user.user_id,
            ).capabilities(user_id=current_user.user_id)
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        return ChatListResponse(
            chats=tuple(
                chat_summary(
                    chat,
                    image_input_enabled=(resolved_attachment_settings.image_input_enabled),
                )
                for chat in chats
            ),
            image_input_enabled=resolved_attachment_settings.image_input_enabled,
            image_generation_enabled=image_capabilities.generation_available,
            image_editing_enabled=(
                resolved_attachment_settings.image_input_enabled and image_capabilities.editing_available
            ),
        )

    @router.post("", response_model=TextChatSummary, status_code=status.HTTP_201_CREATED)
    async def create_chat(
        request_body: ChatCreateRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> TextChatSummary:
        service = service_for(current_user.user_id)
        try:
            chat = await service.create_chat(
                user_id=current_user.user_id,
                agent_id=request_body.agent_id,
                title=request_body.title,
                provider=request_body.provider,
                model=request_body.model,
            )
        except AgentIdentityNotFoundError as exc:
            raise not_found(AGENT_NOT_FOUND) from exc
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        return chat_summary(
            chat,
            image_input_enabled=resolved_attachment_settings.image_input_enabled,
        )

    @router.post(
        "/{chat_id}/attachments",
        response_model=AttachmentSummary,
        status_code=status.HTTP_201_CREATED,
    )
    async def upload_attachment(
        chat_id: str,
        current_user: Annotated[
            AuthenticatedUser,
            Depends(authenticated_user),
        ],
        file: Annotated[UploadFile, File()],
        client_draft_id: Annotated[
            str,
            Form(min_length=1, max_length=64),
        ],
        client_source: Annotated[AttachmentClientSource, Form()],
    ) -> AttachmentSummary:
        service = attachments_for(current_user.user_id)
        try:
            record = await service.upload(
                user_id=current_user.user_id,
                chat_id=chat_id,
                client_draft_id=client_draft_id,
                client_source=client_source,
                upload=file,
            )
        except AttachmentError as exc:
            raise attachment_http_error(exc) from exc
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        return attachment_summary(record)

    @router.delete(
        "/{chat_id}/attachments/{attachment_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_attachment(
        chat_id: str,
        attachment_id: str,
        current_user: Annotated[
            AuthenticatedUser,
            Depends(authenticated_user),
        ],
    ) -> None:
        service = attachments_for(current_user.user_id)
        try:
            await service.delete_staged(
                user_id=current_user.user_id,
                chat_id=chat_id,
                attachment_id=attachment_id,
            )
        except AttachmentError as exc:
            raise attachment_http_error(exc) from exc
        except SQLAlchemyError as exc:
            raise unavailable() from exc

    @router.post(
        "/{chat_id}/images/generate",
        response_model=ChatImageGenerationResponse,
    )
    async def generate_chat_image(
        chat_id: str,
        request_body: ChatImageGenerationRequest,
        current_user: Annotated[
            AuthenticatedUser,
            Depends(authenticated_user),
        ],
    ) -> ChatImageGenerationResponse:
        service = service_for(current_user.user_id)
        chat = await service.get_chat(
            user_id=current_user.user_id,
            chat_id=chat_id,
        )
        if chat is None or chat.agent_id is None:
            raise not_found(CHAT_NOT_FOUND)
        if request_body.attachment_ids and not resolved_attachment_settings.image_input_enabled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Image editing is disabled on this deployment",
            )
        try:
            result = await image_service_for(current_user.user_id).generate(
                user_id=current_user.user_id,
                chat_id=chat_id,
                agent_id=chat.agent_id,
                prompt=request_body.prompt,
                attachment_ids=request_body.attachment_ids,
                client_turn_id=request_body.client_turn_id,
                client_source=request_body.client_source,
            )
        except AttachmentError as exc:
            raise attachment_http_error(exc) from exc
        except ImageGenerationUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        except MissingTextCredentialError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The selected image provider no longer has an active credential",
            ) from exc
        except ImageGenerationProviderError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc
        except SQLAlchemyError as exc:
            raise unavailable() from exc

        output_summary = attachment_summary(result.attachment)
        try:
            input_summaries = await attachments_for(
                current_user.user_id,
            ).summaries_for_transcripts(
                user_id=current_user.user_id,
                chat_id=chat_id,
                transcript_ids=(result.user_message.id,),
            )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        return ChatImageGenerationResponse(
            provider=result.provider,
            model=result.model,
            edited=result.edited,
            user_message=message_response(
                result.user_message,
                attachments=input_summaries.get(result.user_message.id, ()),
            ),
            assistant_message=message_response(
                result.assistant_message,
                attachments=(output_summary,),
            ),
        )

    @router.get("/{chat_id}/attachments/{attachment_id}/content")
    async def get_attachment_content(
        chat_id: str,
        attachment_id: str,
        current_user: Annotated[
            AuthenticatedUser,
            Depends(authenticated_user),
        ],
        variant: AttachmentVariant = AttachmentVariant.FULL,
    ) -> Response:
        service = attachments_for(current_user.user_id)
        try:
            content, mime_type = await service.get_content(
                user_id=current_user.user_id,
                chat_id=chat_id,
                attachment_id=attachment_id,
                variant=variant,
            )
        except AttachmentError as exc:
            raise attachment_http_error(exc) from exc
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        return Response(
            content=content,
            media_type=mime_type,
            headers={
                "Cache-Control": "private, max-age=300",
                "Content-Disposition": "inline",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get("/{chat_id}", response_model=ChatDetailResponse)
    async def get_chat(
        chat_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
        message_limit: Annotated[int, Query(ge=1, le=500)] = 100,
        event_limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ) -> ChatDetailResponse:
        service = service_for(current_user.user_id)
        try:
            chat = await service.get_chat(
                user_id=current_user.user_id,
                chat_id=chat_id,
            )
            if chat is None or chat.agent_id is None:
                raise not_found(CHAT_NOT_FOUND)
            messages = await service.runtime_store.list_transcript_items(
                session_id=chat.id,
                user_id=current_user.user_id,
                agent_id=chat.agent_id,
                limit=message_limit,
            )
            runtime_events = await service.runtime_store.list_runtime_events(
                session_id=chat.id,
                user_id=current_user.user_id,
                agent_id=chat.agent_id,
                limit=event_limit,
            )
            tool_events = await service.runtime_store.list_tool_events(
                session_id=chat.id,
                user_id=current_user.user_id,
                agent_id=chat.agent_id,
                limit=event_limit,
            )
            usage_events = await service.runtime_store.list_usage_events(
                session_id=chat.id,
                user_id=current_user.user_id,
                agent_id=chat.agent_id,
                limit=event_limit,
            )
            delegated_run_records = await service.run_store.list_subagent_runs_for_parent_session(
                user_id=current_user.user_id,
                parent_session_id=chat.id,
                limit=20,
            )
            delegated_runs = await delegated_run_responses(
                service=service,
                runs=tuple(delegated_run_records),
                event_limit=event_limit,
                include_activity=True,
            )
            attachment_summaries = await attachments_for(
                current_user.user_id,
            ).summaries_for_transcripts(
                user_id=current_user.user_id,
                chat_id=chat.id,
                transcript_ids=tuple(record.id for record in messages),
            )
        except HTTPException:
            raise
        except SQLAlchemyError as exc:
            raise unavailable() from exc

        return ChatDetailResponse(
            chat=chat_summary(
                chat,
                image_input_enabled=resolved_attachment_settings.image_input_enabled,
            ),
            messages=tuple(
                message_response(
                    record,
                    attachments=attachment_summaries.get(record.id, ()),
                )
                for record in messages
            ),
            runtime_events=tuple(runtime_event_response(record) for record in runtime_events),
            tool_events=tuple(tool_event_response(record) for record in tool_events),
            usage_events=tuple(usage_event_response(record) for record in usage_events),
            delegated_runs=tuple(delegated_runs),
        )

    @router.get(
        "/{chat_id}/delegated-runs",
        response_model=ChatDelegatedRunListResponse,
    )
    async def get_chat_delegated_runs(
        chat_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> ChatDelegatedRunListResponse:
        service = service_for(current_user.user_id)
        try:
            chat = await service.get_chat(
                user_id=current_user.user_id,
                chat_id=chat_id,
            )
            if chat is None or chat.agent_id is None:
                raise not_found(CHAT_NOT_FOUND)
            delegated_run_records = await service.run_store.list_subagent_runs_for_parent_session(
                user_id=current_user.user_id,
                parent_session_id=chat.id,
                limit=20,
            )
            delegated_runs = await delegated_run_responses(
                service=service,
                runs=tuple(delegated_run_records),
                event_limit=1,
                include_activity=False,
            )
        except HTTPException:
            raise
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        return ChatDelegatedRunListResponse(delegated_runs=delegated_runs)

    @router.patch("/{chat_id}", response_model=TextChatSummary)
    async def update_chat(
        chat_id: str,
        request_body: ChatUpdateRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> TextChatSummary:
        service = service_for(current_user.user_id)
        try:
            chat = await service.update_chat(
                user_id=current_user.user_id,
                chat_id=chat_id,
                title=request_body.title,
                provider=request_body.provider,
                model=request_body.model,
            )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        if chat is None:
            raise not_found(CHAT_NOT_FOUND)
        return chat_summary(
            chat,
            image_input_enabled=resolved_attachment_settings.image_input_enabled,
        )

    @router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_chat(
        chat_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> None:
        service = service_for(current_user.user_id)
        try:
            chat = await service.delete_chat(
                user_id=current_user.user_id,
                chat_id=chat_id,
            )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        if chat is None:
            raise not_found(CHAT_NOT_FOUND)

    @router.post("/{chat_id}/messages/stream")
    async def stream_message(
        chat_id: str,
        request_body: ChatMessageStreamRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> StreamingResponse:
        service = service_for(current_user.user_id)
        preparation_started = time.perf_counter()
        try:
            prepared_turn = await service.prepare_stream_message(
                user_id=current_user.user_id,
                chat_id=chat_id,
                text=request_body.text,
                attachment_ids=request_body.attachment_ids,
                client_turn_id=request_body.client_turn_id,
                current_location=request_body.location,
            )
        except LookupError as exc:
            raise not_found(CHAT_NOT_FOUND) from exc
        except AttachmentError as exc:
            raise attachment_http_error(exc) from exc
        if (
            request_body.attachment_ids
            and text_model_for_image_input(
                TextProvider(prepared_turn.chat.provider),
                prepared_turn.chat.model,
            )
            is None
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The selected model does not support image input",
            )
        record_text_timing(
            "VoxosTextRoutePreparation",
            {
                "duplicate": prepared_turn.duplicate,
                "attachment_count": len(request_body.attachment_ids),
                "total_ms": ((time.perf_counter() - preparation_started) * 1000),
            },
        )

        return StreamingResponse(
            stream_events(
                service=service,
                user_id=current_user.user_id,
                chat_id=chat_id,
                text=request_body.text,
                attachment_ids=request_body.attachment_ids,
                client_turn_id=request_body.client_turn_id,
                prepared_turn=prepared_turn,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    return router


def build_text_chat_service(
    *,
    session_factory: AsyncSessionFactory,
    secret_vault: UserSecretVaultService,
    http_client: httpx.AsyncClient,
    public_base_url: str | None = None,
    voice_settings: VoiceAgentSettings | None = None,
    text_provider_client: TextProviderClient | None = None,
    attachment_service: AttachmentService | None = None,
    image_generation_client: ImageGenerationProviderClient | None = None,
    browser_account_settings: BrowserAccountSettings | None = None,
    browser_account_provider: BrowserAccountProviderAdapter | None = None,
    push_settings: PushNotificationSettings | None = None,
    outbound_endpoint_policy: OutboundEndpointPolicy | None = None,
    mcp_tool_catalog_cache: McpToolCatalogCache | None = None,
) -> TextChatService:
    resolved_browser_account_settings = browser_account_settings or BrowserAccountSettings()
    resolved_push_settings = push_settings or PushNotificationSettings()
    runtime_store = SqlModelAgentRuntimeStore(
        session_factory=session_factory,
        chat_notifications_enabled=resolved_push_settings.enabled,
        chat_notification_max_attempts=resolved_push_settings.max_attempts,
    )
    run_store = SqlModelAgentRunStore(
        session_factory=session_factory,
        terminal_notifications_enabled=resolved_push_settings.enabled,
        terminal_notification_max_attempts=resolved_push_settings.max_attempts,
    )
    run_chat_service = RunChatService(
        session_factory=session_factory,
        runtime_store=runtime_store,
        run_store=run_store,
    )
    authorization_service = SqlModelToolAuthorizationService(
        session_factory=session_factory,
    )
    routing_service = SqlModelToolRoutingService(session_factory=session_factory)
    effective_tool_policy_service = EffectiveToolPolicyService(
        authorization_service,
        routing_service,
    )
    memory_service = AgentMemoryService(session_factory=session_factory)
    skill_service = AgentSkillService(session_factory=session_factory)
    planning_service = AgentPlanningService(session_factory=session_factory)
    identity_service = AgentIdentityService(session_factory=session_factory)
    subagent_model_policy = TextSubAgentModelPolicy(session_factory=session_factory)
    browser_provider = browser_account_provider or CompositeBrowserAccountProvider(
        browser_use=BrowserUseCloudProvider(
            http_client=http_client,
            settings=resolved_browser_account_settings,
        ),
        browserless=BrowserlessProvider(
            resolved_browser_account_settings,
        ),
        stagehand_local=StagehandLocalProvider(
            http_client=http_client,
            settings=resolved_browser_account_settings,
        ),
    )
    browser_model_resolver = BrowserTaskModelCredentialResolver(
        session_factory=session_factory,
        secret_vault=secret_vault,
    )
    browser_profile_service = BrowserAccountService(
        session_factory=session_factory,
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
    image_generation_service = (
        ImageGenerationService(
            session_factory=session_factory,
            secret_vault=secret_vault,
            attachment_service=attachment_service,
            provider_client=(image_generation_client or HttpImageGenerationProviderClient(http_client)),
        )
        if attachment_service is not None
        else None
    )
    resolved_text_client = text_provider_client or OpenAICompatibleTextProvider(
        http_client=http_client,
        outbound_endpoint_policy=outbound_endpoint_policy or OutboundEndpointPolicy.from_settings(),
    )
    history_service = TenantHistoryService(session_factory, authorization_service)
    improvement_service = AgentImprovementService(
        session_factory,
        history_service,
        memory_service,
        skill_service,
        TextLearningModel(session_factory, secret_vault, resolved_text_client),
    )
    tool_providers: list[AgentToolProvider] = [
        InternalAgentToolProvider(
            memory_service=memory_service,
            skill_service=skill_service,
            planning_service=planning_service,
            history_service=history_service,
            improvement_service=improvement_service,
            contact_session_factory=session_factory,
            product_docs_service=EnvironmentProductDocRetrievalService(
                session_factory=session_factory,
                http_client=http_client,
            ),
            authorization_service=authorization_service,
        ),
        AgentScheduleToolProvider(
            schedule_store=AgentScheduleStore(
                session_factory=session_factory,
                run_store=run_store,
                identity_service=identity_service,
                run_chat_service=run_chat_service,
            ),
            authorization_service=authorization_service,
        ),
        UserMcpAgentToolProvider(
            session_factory=session_factory,
            secret_vault=secret_vault,
            authorization_service=authorization_service,
            http_client=http_client,
            tool_catalog_cache=mcp_tool_catalog_cache,
        ),
        BrowserAgentToolProvider(
            profile_service=browser_profile_service,
            runtime_service=browser_runtime_service,
            authorization_service=authorization_service,
        ),
        SubAgentToolProvider(
            subagent_service=SubAgentService(
                run_store=run_store,
                identity_service=identity_service,
                effective_tool_policy_service=effective_tool_policy_service,
                model_policy=subagent_model_policy,
            ),
            model_policy=subagent_model_policy,
            authorization_service=authorization_service,
        ),
    ]
    if image_generation_service is not None:
        tool_providers.append(
            ImageGenerationAgentToolProvider(
                service=image_generation_service,
                authorization_service=authorization_service,
            ),
        )
    if public_base_url is not None:
        call_demand_coordinator: LiveKitCallDemandCoordinator | None = None
        if voice_settings is not None:
            call_demand_coordinator = LiveKitCallDemandCoordinator(
                call_router=LiveKitCallRouter(
                    session_factory=session_factory,
                    settings=voice_settings,
                    token_template=LiveKitVoiceTokenService.from_settings(
                        voice_settings,
                    ),
                ),
                secret_vault=secret_vault,
            )
        tool_providers.append(
            IntegrationAgentToolProvider(
                session_factory=session_factory,
                secret_vault=secret_vault,
                public_base_url=public_base_url,
                authorization_service=authorization_service,
                voice_call_preflight=(
                    LiveKitCallPreflight(
                        settings=voice_settings,
                        secret_vault=secret_vault,
                    )
                    if voice_settings is not None
                    else None
                ),
                voice_worker_demand_requester=call_demand_coordinator,
                voice_worker_readiness_waiter=(
                    call_demand_coordinator.wait if call_demand_coordinator is not None else None
                ),
                voice_agent_dispatcher=(
                    call_demand_coordinator.dispatch if call_demand_coordinator is not None else None
                ),
                voice_agent_readiness_waiter=(
                    call_demand_coordinator.wait_for_agent_ready if call_demand_coordinator is not None else None
                ),
                voice_worker_demand_canceller=(
                    call_demand_coordinator.cancel if call_demand_coordinator is not None else None
                ),
            ),
        )
    return TextChatService(
        session_factory=session_factory,
        secret_vault=secret_vault,
        runtime_store=runtime_store,
        run_store=run_store,
        run_chat_service=run_chat_service,
        effective_tool_policy_service=effective_tool_policy_service,
        instruction_provider=PersonaAgentInstructionProvider(
            persona_service=AgentPersonaService(),
            identity_service=identity_service,
            memory_service=memory_service,
            skill_service=skill_service,
            planning_service=planning_service,
            tool_policy_context=ToolPolicyContext(
                available_tools=(
                    *INTERNAL_AGENT_MCP_TOOL_NAMES,
                    DELEGATE_AGENT_TASK_TOOL_NAME,
                    GENERATE_IMAGE_TOOL_NAME,
                ),
                confirmation_required_actions=(
                    DELEGATE_AGENT_TASK_TOOL_NAME,
                    "create_agent_memory",
                    "archive_agent_memory",
                    "create_user_skill",
                    "update_user_skill",
                    "archive_user_skill",
                    "complete_task_plan",
                ),
            ),
            tool_authorization_service=authorization_service,
        ),
        tool_provider=CompositeAgentToolProvider(
            providers=tuple(tool_providers),
        ),
        provider_client=resolved_text_client,
        attachment_service=attachment_service,
        image_editing_available=(
            image_generation_service.editing_available if image_generation_service is not None else None
        ),
    )


async def stream_events(
    *,
    service: TextChatService,
    user_id: str,
    chat_id: str,
    text: str,
    attachment_ids: tuple[str, ...] = (),
    client_turn_id: str | None = None,
    prepared_turn: PreparedTextChatTurn | None = None,
) -> AsyncIterator[bytes]:
    stream_started = time.perf_counter()
    first_event_ms: float | None = None
    first_delta_ms: float | None = None
    event_count = 0
    byte_count = 0
    downstream_pause_ms = 0.0
    max_downstream_pause_ms = 0.0
    stream_status = "failed"
    try:
        async for event in service.stream_message(
            user_id=user_id,
            chat_id=chat_id,
            text=text,
            attachment_ids=attachment_ids,
            client_turn_id=client_turn_id,
            prepared_turn=prepared_turn,
        ):
            elapsed_ms = (time.perf_counter() - stream_started) * 1000
            if first_event_ms is None:
                first_event_ms = elapsed_ms
            if first_delta_ms is None and event.kind == AgentEventKind.MESSAGE_DELTA:
                first_delta_ms = elapsed_ms
            frame = sse_frame(
                event_id=event.id,
                event_name=event.kind.value,
                payload={
                    "id": event.id,
                    "kind": event.kind.value,
                    "session_id": event.session_id,
                    "run_id": event.run_id,
                    "payload": event.payload,
                    "created_at": iso_datetime(event.created_at),
                },
            )
            event_count += 1
            byte_count += len(frame)
            pause_started = time.perf_counter()
            try:
                yield frame
            finally:
                pause_ms = (time.perf_counter() - pause_started) * 1000
                downstream_pause_ms += pause_ms
                max_downstream_pause_ms = max(max_downstream_pause_ms, pause_ms)
        stream_status = "completed"
    except (asyncio.CancelledError, GeneratorExit):
        stream_status = "cancelled"
        raise
    finally:
        record_text_timing(
            "VoxosTextSseStream",
            {
                "status": stream_status,
                "first_event_ms": first_event_ms,
                "first_delta_ms": first_delta_ms,
                "event_count": event_count,
                "byte_count": byte_count,
                "downstream_pause_ms": downstream_pause_ms,
                "max_downstream_pause_ms": max_downstream_pause_ms,
                "wall_ms": (time.perf_counter() - stream_started) * 1000,
            },
        )


def sse_frame(
    *,
    event_id: str,
    event_name: str,
    payload: dict[str, object],
) -> bytes:
    encoded = orjson.dumps(payload).decode()
    return f"id: {event_id}\nevent: {event_name}\ndata: {encoded}\n\n".encode()


def chat_summary(
    record: AgentSessionRecord,
    *,
    image_input_enabled: bool = True,
) -> TextChatSummary:
    return TextChatSummary(
        id=record.id,
        agent_id=record.agent_id or "",
        title=record.title,
        provider=TextProvider(record.provider),
        model=record.model,
        supports_image_input=(
            image_input_enabled
            and text_model_for_image_input(
                TextProvider(record.provider),
                record.model,
            )
            is not None
        ),
        status=record.status,
        started_at=record.started_at,
        updated_at=record.updated_at,
    )


def message_response(
    record: AgentTranscriptRecord,
    *,
    attachments: tuple[AttachmentSummary, ...] = (),
) -> ChatMessageResponse:
    return ChatMessageResponse(
        id=record.id,
        role=record.role,
        text=record.text,
        source=record.source,
        interrupted=record.interrupted,
        is_final=record.is_final,
        client_turn_id=record.client_turn_id,
        created_at=iso_datetime(record.created_at),
        attachments=attachments,
    )


def runtime_event_response(record: AgentRuntimeEventRecord) -> ChatRuntimeEventResponse:
    return ChatRuntimeEventResponse(
        id=record.id,
        kind=record.kind,
        payload_json=record.payload_json,
        created_at=iso_datetime(record.created_at),
    )


def tool_event_response(record: AgentToolEventRecord) -> ChatToolEventResponse:
    return ChatToolEventResponse(
        id=record.id,
        call_id=record.call_id,
        name=record.name,
        arguments_json=record.arguments_json,
        output_text=record.output_text,
        is_error=record.is_error,
        created_at=iso_datetime(record.created_at),
    )


def usage_event_response(record: AgentUsageRecord) -> ChatUsageEventResponse:
    return ChatUsageEventResponse(
        id=record.id,
        usage_type=record.usage_type,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        total_tokens=record.total_tokens,
        raw_usage_json=record.raw_usage_json,
        created_at=iso_datetime(record.created_at),
    )


async def delegated_run_responses(
    *,
    service: TextChatService,
    runs: tuple[AgentRunRecord, ...],
    event_limit: int,
    include_activity: bool,
) -> tuple[ChatDelegatedRunResponse, ...]:
    if not runs:
        return ()
    user_id = runs[0].user_id
    session_ids = tuple(run.chat_id for run in runs if run.chat_id is not None)
    transcripts = await service.runtime_store.list_transcript_items_for_sessions(
        session_ids=session_ids,
        user_id=user_id,
        limit_per_session=50,
    )
    runtime_events: tuple[AgentRuntimeEventRecord, ...] = ()
    tool_events: tuple[AgentToolEventRecord, ...] = ()
    if include_activity:
        runtime_events, tool_events = await asyncio.gather(
            service.runtime_store.list_runtime_events_for_sessions(
                session_ids=session_ids,
                user_id=user_id,
                limit_per_session=event_limit,
            ),
            service.runtime_store.list_tool_events_for_sessions(
                session_ids=session_ids,
                user_id=user_id,
                limit_per_session=event_limit,
            ),
        )

    transcripts_by_run = group_delegated_records(transcripts)
    runtime_events_by_run = group_delegated_records(runtime_events)
    tool_events_by_run = group_delegated_records(tool_events)
    return tuple(
        delegated_run_response(
            run=run,
            transcripts=transcripts_by_run.get((run.chat_id, run.agent_id), ()),
            runtime_events=runtime_events_by_run.get((run.chat_id, run.agent_id), ()),
            tool_events=tool_events_by_run.get((run.chat_id, run.agent_id), ()),
        )
        for run in runs
    )


def group_delegated_records(
    records: tuple[DelegatedRecordT, ...],
) -> dict[tuple[str | None, str | None], tuple[DelegatedRecordT, ...]]:
    grouped: dict[tuple[str | None, str | None], list[DelegatedRecordT]] = {}
    for record in records:
        grouped.setdefault((record.session_id, record.agent_id), []).append(record)
    return {key: tuple(values) for key, values in grouped.items()}


def delegated_run_response(
    *,
    run: AgentRunRecord,
    transcripts: tuple[AgentTranscriptRecord, ...],
    runtime_events: tuple[AgentRuntimeEventRecord, ...],
    tool_events: tuple[AgentToolEventRecord, ...],
) -> ChatDelegatedRunResponse:
    output_record = next(
        (record for record in reversed(transcripts) if record.role == "assistant"),
        None,
    )
    task_prompt = run.payload_json.get("task_prompt")
    provider = run.payload_json.get("provider")
    model = run.payload_json.get("model")
    return ChatDelegatedRunResponse(
        id=run.id,
        status=run.status,
        task_prompt=(task_prompt if isinstance(task_prompt, str) else "Delegated task"),
        provider=provider if isinstance(provider, str) else None,
        model=model if isinstance(model, str) else None,
        chat_id=run.chat_id,
        output_text=None if output_record is None else output_record.text,
        output_created_at=(None if output_record is None else iso_datetime(output_record.created_at)),
        runtime_events=tuple(runtime_event_response(record) for record in runtime_events),
        tool_events=tuple(tool_event_response(record) for record in tool_events),
        created_at=iso_datetime(run.created_at),
        completed_at=(None if run.completed_at is None else iso_datetime(run.completed_at)),
    )


def not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Chats are unavailable",
    )


def attachment_http_error(exc: AttachmentError) -> HTTPException:
    if isinstance(exc, AttachmentNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attachment was not found",
        )
    if isinstance(
        exc,
        AttachmentConflictError | AttachmentModelUnsupportedError,
    ):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    if isinstance(exc, AttachmentValidationError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Chat attachments are unavailable",
    )

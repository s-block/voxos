import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Literal

import orjson
from anyio import CancelScope
from pydantic import ValidationError as PydanticValidationError

from voxos.agent_core.instructions import (
    AgentInstructionSessionSettings,
    PersonaAgentInstructionProvider,
)
from voxos.agent_core.mcp_tools import prepare_agent_tool_provider
from voxos.agent_core.models import (
    AgentEvent,
    AgentEventKind,
    AgentRunContext,
    AgentTool,
    AgentToolActivity,
    AgentToolAttachmentArtifact,
    AgentToolResult,
)
from voxos.agent_core.models import AgentToolProvider as CoreAgentToolProvider
from voxos.agent_core.response_profiles import (
    CommunicationMedium,
    channel_for_communication_medium,
    communication_medium_for_channel,
    response_profile,
)
from voxos.agent_core.time_context import (
    Clock,
    ConversationTimelineContext,
    SystemClock,
    TranscriptTimelineEntry,
)
from voxos.agent_core.tool_catalog import (
    TOOL_CALL_NAME,
    TOOL_DESCRIBE_NAME,
    TOOL_SEARCH_NAME,
    RunScopedToolCatalogProvider,
    ToolNotExposedError,
)
from voxos.agent_identity.models import AgentIdentity, AgentScope
from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.effective_tool_policy import (
    EffectiveToolPolicyService,
    effective_tool_policy_from_json,
    effective_tool_policy_id,
)
from voxos.agent_runtime.models import (
    AgentRuntimeEventKind,
    AgentStateEvent,
    AgentToolEvent,
    AgentTranscriptItem,
    AgentTranscriptRole,
    AgentTranscriptSource,
    AgentUsageSnapshot,
    JsonObject,
)
from voxos.agent_runtime.records import (
    AgentRunRecord,
    AgentSessionRecord,
    AgentToolEventRecord,
    AgentTranscriptRecord,
)
from voxos.agent_runtime.run_models import (
    AgentRunEventKind,
    AgentRunFailureCategory,
    AgentRunKind,
)
from voxos.agent_runtime.run_store import AgentRunCancelledError, SqlModelAgentRunStore
from voxos.agent_runtime.storage import SqlModelAgentRuntimeStore
from voxos.agent_runtime.tool_execution import (
    ToolArgumentSchemaError,
    ToolExecutionSecurityError,
    ToolSchemaDefinitionError,
    prune_unsupported_tool_arguments,
    validate_llm_tool_arguments,
    validate_tool_arguments_schema,
)
from voxos.attachments.records import MessageAttachmentRecord
from voxos.attachments.service import (
    AttachmentModelUnsupportedError,
    AttachmentService,
    AttachmentValidationError,
)
from voxos.attachments.telemetry import record_provider_image_count
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import new_record_id, utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.location.models import ClientLocation, fresh_client_location
from voxos.mcp.errors import McpConnectionTestError, McpToolCallRejectedError
from voxos.place_cards.models import (
    PlaceCardArtifact,
    selected_place_cards_for_message,
)
from voxos.text.errors import MissingTextCredentialError, TextPreferencesNotFoundError
from voxos.text.models import (
    ResolvedTextCredentials,
    TextProvider,
    TextReasoningMode,
    UserTextPreferences,
    text_model_for_image_input,
    text_model_supports_image_input,
)
from voxos.text.providers import (
    ProviderInputImage,
    ProviderInputText,
    ProviderMessage,
    TextProviderClient,
    TextProviderEventKind,
    TextProviderIncompleteResponseError,
    TextProviderRequest,
    TextProviderRequestError,
    TextProviderResponseSummary,
    TextProviderToolCall,
    TextProviderTransportError,
    TextToolChoice,
    provider_function_call_items,
    provider_function_call_output_item,
)
from voxos.text.run_chat_service import (
    RunChatService,
    default_text_preferences,
    normalized_title,
)
from voxos.text.storage import UserTextCredentialService, UserTextPreferenceService
from voxos.text.telemetry import record_text_timing

logger = logging.getLogger(__name__)
TEXT_CHAT_CHANNEL = "text"
MAX_TOOL_ROUNDS = 4
MAX_TOOL_DISCOVERY_ROUNDS = 4
TOOL_DISCOVERY_NAMES = frozenset({TOOL_SEARCH_NAME, TOOL_DESCRIBE_NAME})
INLINE_RUN_LEASE_SECONDS = 900
MAX_DETAILED_TOOL_CONTEXT_TOOLS = 24
MAX_TOOL_CONTEXT_DESCRIPTION_CHARS = 240
MAX_ADDITIONAL_TOOL_CONTEXT_DESCRIPTION_CHARS = 120
MAX_RECENT_TOOL_EVIDENCE_EVENTS = 20
MAX_VISIBLE_RECENT_TOOL_EXECUTIONS = 5
MAX_PARALLEL_TOOL_CALLS = 8
MAX_TOOL_ERROR_MESSAGE_CHARS = 1000
MAX_EMPTY_PROVIDER_RESPONSE_RETRIES = 1
MAX_TRANSIENT_PROVIDER_RETRIES = 1
MAX_TOOL_PROTOCOL_REPAIR_RETRIES = 1
TRANSIENT_PROVIDER_RETRY_DELAY_SECONDS = 0.5
IMAGE_HISTORY_OMITTED = "[Earlier image attachment omitted because the current model does not support image input.]"
IMAGE_TOOL_REFERENCES_AVAILABLE = (
    "[Private image references attached to this message are available to the "
    "generate_image tool. The current text model has not inspected their pixels.]"
)
GENERATED_IMAGE_RECOVERY_TEXT = (
    "The image was generated successfully, but the rest of the response could not be completed."
)
EMPTY_PROVIDER_RESPONSE_REPAIR_PROMPT = (
    "Your last response did not include a final answer or a structured tool call. "
    "Continue this turn with a useful response. When the request requires current "
    "external information or a named specialist capability, use the appropriate "
    "available tool first. Do not return only internal reasoning or an empty response."
)
TOOL_BUDGET_EXHAUSTED_PROMPT = (
    "The tool budget for this turn is now spent, and no tools are attached to this "
    "request. Any earlier instruction to call a tool no longer applies. Do not emit "
    "tool call syntax in any form, including XML-style or JSON-style markup, because "
    "nothing will execute it and the text is shown to the user verbatim. Answer now "
    "from the tool results already gathered, and say plainly what you could not confirm."
)
TOOL_PROTOCOL_REPAIR_PROMPT = (
    "Your last response used textual tool-call markup after tools were withdrawn. "
    "Do not try another tool call or describe tool-call syntax. Give the best final "
    "answer supported by the completed tool results, and state any remaining uncertainty."
)
XML_TOOL_CALL_START = "<tool_call>"
XML_TOOL_CALL_END = "</tool_call>"
_AGENTIC_RUN_KINDS = frozenset(
    {
        AgentRunKind.MANUAL_AGENT_TASK.value,
        AgentRunKind.WEBHOOK_AGENT_TASK.value,
        AgentRunKind.SCHEDULED_AGENT_TURN.value,
        AgentRunKind.SUBAGENT.value,
    },
)


@dataclass(frozen=True, slots=True)
class TextRunSetup:
    preferences: UserTextPreferences
    credentials: ResolvedTextCredentials | None


@dataclass(frozen=True, slots=True)
class PreparedTextChatTurn:
    chat: AgentSessionRecord
    text: str
    provider: TextProvider
    model: str
    current_location: ClientLocation | None = None
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class PreparedTextToolCatalog:
    provider: RunScopedToolCatalogProvider
    tools: tuple[AgentTool, ...]
    tool_provider_ms: float
    tool_catalog_ms: float


@dataclass(frozen=True, slots=True)
class PreparedToolCall:
    tool_call: TextProviderToolCall
    fingerprint: str
    tool: AgentTool | None = None
    activity_tool: AgentTool | None = None
    preparation_error: Exception | None = None


@dataclass(frozen=True, slots=True)
class ToolCallExecutionOutcome:
    prepared_call: PreparedToolCall
    result: AgentToolResult | None = None
    error: Exception | None = None


class TextProviderEmptyFinalResponseError(RuntimeError):
    """Raised when a provider emits no final text or structured tool call."""

    def __init__(self, summary: TextProviderResponseSummary) -> None:
        self.summary = summary
        if summary.saw_reasoning_content:
            message = "Text provider returned reasoning without a final response or structured tool call"
        else:
            message = "Text provider returned an empty response without invoking a tool"
        super().__init__(message)


class TextProviderToolProtocolError(RuntimeError):
    """Raised when a provider emits text-form tool syntax instead of a native call."""

    def __init__(self) -> None:
        super().__init__(
            "Text provider emitted a textual tool call without a structured tool call",
        )


def text_reasoning_mode_for_run_kind(run_kind: str) -> TextReasoningMode:
    """Keep interactive conversations responsive while reserving reasoning for agents."""

    if run_kind in _AGENTIC_RUN_KINDS:
        return TextReasoningMode.AGENTIC
    return TextReasoningMode.FAST


class ArtifactOutcome(StrEnum):
    CONSUMED = "consumed"
    NOT_AN_ARTIFACT = "not_an_artifact"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True, slots=True)
class ArtifactScan:
    outcome: ArtifactOutcome
    end: int = 0


class ToolProtocolArtifactFilter:
    """Strip tool calls a provider emitted as prose instead of as a structured call.

    Two shapes are recognised. Harmony-style providers write ``to=functions.<name>``
    followed by a JSON argument object. Nemotron, Qwen and similar templates write
    XML-style markup such as ``<tool_call><function=<name>>…</tool_call>``. Both reach
    the caller as ordinary content, so without this filter they are persisted and shown
    to the user as the assistant's answer.
    """

    def __init__(self, tool_names: frozenset[str]) -> None:
        self._markers = tuple(
            sorted(
                (f"to=functions.{tool_name}".lower() for tool_name in tool_names),
                key=len,
                reverse=True,
            ),
        )
        self._tool_name_patterns = tuple(
            re.compile(rf"(?<![a-z0-9_.-]){re.escape(tool_name.lower())}(?![a-z0-9_.-])") for tool_name in tool_names
        )
        self._openers = (*self._markers, XML_TOOL_CALL_START)
        self._pending = ""
        self._removed_tool_call_artifact = False

    @property
    def removed_tool_call_artifact(self) -> bool:
        return self._removed_tool_call_artifact

    def feed(self, delta: str) -> str:
        self._pending += delta
        return self._drain(final=False)

    def flush(self) -> str:
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> str:
        emitted: list[str] = []
        while self._pending:
            match_start, opener = self._first_opener()
            if opener is None:
                safe_length = len(self._pending) if final else len(self._pending) - self._opener_prefix_length()
                if safe_length <= 0:
                    break
                emitted.append(self._pending[:safe_length])
                self._pending = self._pending[safe_length:]
                continue

            scan = self._scan_artifact(start=match_start, opener=opener, final=final)
            if scan.outcome == ArtifactOutcome.CONSUMED:
                emitted.append(self._pending[:match_start])
                self._removed_tool_call_artifact = True
                self._pending = self._pending[scan.end :]
                continue
            if scan.outcome == ArtifactOutcome.NOT_AN_ARTIFACT:
                emitted.append(self._pending[: match_start + 1])
                self._pending = self._pending[match_start + 1 :]
                continue
            if final:
                # The stream ended mid-artifact. Truncated markup is still markup, so
                # drop it rather than leaking a half-written tool call to the user.
                emitted.append(self._pending[:match_start])
                self._removed_tool_call_artifact = True
                self._pending = ""
                break
            emitted.append(self._pending[:match_start])
            self._pending = self._pending[match_start:]
            break
        return "".join(emitted)

    def _scan_artifact(self, *, start: int, opener: str, final: bool) -> ArtifactScan:
        if opener in self._markers:
            return self._scan_json_artifact(start=start, marker=opener)
        return self._scan_xml_artifact(start=start, final=final)

    def _scan_json_artifact(self, *, start: int, marker: str) -> ArtifactScan:
        object_start = self._pending.find("{", start + len(marker))
        if object_start < 0:
            return ArtifactScan(outcome=ArtifactOutcome.INCOMPLETE)
        object_end = json_object_end(self._pending, start=object_start)
        if object_end is None:
            return ArtifactScan(outcome=ArtifactOutcome.INCOMPLETE)
        try:
            payload = orjson.loads(self._pending[object_start:object_end])
        except orjson.JSONDecodeError:
            return ArtifactScan(outcome=ArtifactOutcome.NOT_AN_ARTIFACT)
        if not isinstance(payload, dict):
            return ArtifactScan(outcome=ArtifactOutcome.NOT_AN_ARTIFACT)
        return ArtifactScan(outcome=ArtifactOutcome.CONSUMED, end=object_end)

    def _scan_xml_artifact(self, *, start: int, final: bool) -> ArtifactScan:
        lowered = self._pending.lower()
        # The tag itself is excluded from the scan: "<tool_call>" contains the name of
        # the tool_call bridge tool, so including it would match every such tag.
        body_start = start + len(XML_TOOL_CALL_START)
        close_index = lowered.find(XML_TOOL_CALL_END, body_start)
        if close_index < 0:
            # Prose can legitimately contain an angle bracket, so the tag only counts
            # once its body names a tool exposed in this run. Mid-stream the body may
            # simply not have arrived yet, so hold rather than judge it.
            if self._names_exposed_tool(lowered[body_start:]):
                return ArtifactScan(outcome=ArtifactOutcome.INCOMPLETE)
            if final:
                return ArtifactScan(outcome=ArtifactOutcome.NOT_AN_ARTIFACT)
            return ArtifactScan(outcome=ArtifactOutcome.INCOMPLETE)
        if not self._names_exposed_tool(lowered[body_start:close_index]):
            return ArtifactScan(outcome=ArtifactOutcome.NOT_AN_ARTIFACT)
        return ArtifactScan(
            outcome=ArtifactOutcome.CONSUMED,
            end=close_index + len(XML_TOOL_CALL_END),
        )

    def _names_exposed_tool(self, region: str) -> bool:
        return any(pattern.search(region) for pattern in self._tool_name_patterns)

    def _first_opener(self) -> tuple[int, str | None]:
        lowered = self._pending.lower()
        matches = ((lowered.find(opener), opener) for opener in self._openers if lowered.find(opener) >= 0)
        return min(matches, default=(0, None), key=lambda item: item[0])

    def _opener_prefix_length(self) -> int:
        lowered = self._pending.lower()
        return max(
            (
                prefix_length
                for opener in self._openers
                for prefix_length in range(min(len(opener) - 1, len(lowered)), 0, -1)
                if lowered.endswith(opener[:prefix_length])
            ),
            default=0,
        )


def json_object_end(value: str, *, start: int) -> int | None:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(value)):
        character = value[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def parallel_tool_call_batches(
    prepared_calls: tuple[PreparedToolCall, ...],
) -> tuple[tuple[PreparedToolCall, ...], ...]:
    return tuple(
        prepared_calls[start : start + MAX_PARALLEL_TOOL_CALLS]
        for start in range(0, len(prepared_calls), MAX_PARALLEL_TOOL_CALLS)
    )


async def run_tool_call_batch(
    *,
    tool_provider: CoreAgentToolProvider,
    context: AgentRunContext,
    prepared_calls: tuple[PreparedToolCall, ...],
) -> tuple[ToolCallExecutionOutcome, ...]:
    tasks = tuple(
        asyncio.create_task(
            run_tool_call(
                tool_provider=tool_provider,
                context=context,
                prepared_call=prepared_call,
            )
        )
        for prepared_call in prepared_calls
    )
    try:
        return tuple(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def run_tool_call(
    *,
    tool_provider: CoreAgentToolProvider,
    context: AgentRunContext,
    prepared_call: PreparedToolCall,
) -> ToolCallExecutionOutcome:
    tool_call = prepared_call.tool_call
    if prepared_call.preparation_error is not None:
        return ToolCallExecutionOutcome(
            prepared_call=prepared_call,
            error=prepared_call.preparation_error,
        )
    try:
        result = await tool_provider.execute_tool(
            context=context,
            tool_name=tool_call.name,
            call_id=tool_call.call_id,
            arguments_json=tool_call.arguments_json,
        )
    except Exception as exc:
        if not isinstance(
            exc,
            (
                McpConnectionTestError,
                McpToolCallRejectedError,
                PermissionError,
                PydanticValidationError,
                ToolArgumentSchemaError,
                ToolExecutionSecurityError,
            ),
        ):
            logger.exception(
                "Unexpected agent tool execution failure",
                extra={
                    "run_id": context.run_id,
                    "user_id": context.user_id,
                    "agent_id": context.agent_id,
                    "chat_id": context.session_id,
                    "tool_name": tool_call.name,
                },
            )
        return ToolCallExecutionOutcome(
            prepared_call=prepared_call,
            error=exc,
        )
    return ToolCallExecutionOutcome(
        prepared_call=prepared_call,
        result=result,
    )


@dataclass(frozen=True, slots=True)
class TextChatService:
    session_factory: AsyncSessionFactory
    secret_vault: UserSecretVaultService
    runtime_store: SqlModelAgentRuntimeStore
    run_store: SqlModelAgentRunStore
    run_chat_service: RunChatService
    effective_tool_policy_service: EffectiveToolPolicyService
    instruction_provider: PersonaAgentInstructionProvider
    tool_provider: CoreAgentToolProvider
    provider_client: TextProviderClient
    attachment_service: AttachmentService | None = None
    image_editing_available: Callable[[str], Awaitable[bool]] | None = None
    clock: Clock = field(default_factory=SystemClock)

    async def list_chats(
        self,
        *,
        user_id: str,
        agent_id: str | None = None,
    ) -> tuple[AgentSessionRecord, ...]:
        return await self.runtime_store.list_sessions(
            user_id=user_id,
            agent_id=agent_id,
            channel=TEXT_CHAT_CHANNEL,
        )

    async def create_chat(
        self,
        *,
        user_id: str,
        agent_id: str | None,
        title: str | None,
        provider: TextProvider | None,
        model: str | None,
    ) -> AgentSessionRecord:
        agent = await self._resolve_agent(user_id=user_id, agent_id=agent_id)
        return await self.run_chat_service.create_chat(
            user_id=user_id,
            agent_id=agent.id,
            title=normalized_title(title) or "New chat",
            provider=provider,
            model=model,
        )

    async def get_chat(
        self,
        *,
        user_id: str,
        chat_id: str,
    ) -> AgentSessionRecord | None:
        return await self.runtime_store.get_session(
            session_id=chat_id,
            user_id=user_id,
            channel=TEXT_CHAT_CHANNEL,
        )

    async def update_chat(
        self,
        *,
        user_id: str,
        chat_id: str,
        title: str | None,
        provider: TextProvider | None,
        model: str | None,
    ) -> AgentSessionRecord | None:
        chat = await self.get_chat(user_id=user_id, chat_id=chat_id)
        if chat is None or chat.agent_id is None:
            return None
        return await self.runtime_store.update_session_chat_settings(
            session_id=chat_id,
            user_id=user_id,
            agent_id=chat.agent_id,
            title=normalized_title(title) if title is not None else None,
            provider=None if provider is None else provider.value,
            model=model,
        )

    async def delete_chat(
        self,
        *,
        user_id: str,
        chat_id: str,
    ) -> AgentSessionRecord | None:
        chat = await self.get_chat(user_id=user_id, chat_id=chat_id)
        if chat is None or chat.agent_id is None:
            return None
        deleted = await self.runtime_store.soft_delete_session(
            session_id=chat_id,
            user_id=user_id,
            agent_id=chat.agent_id,
        )
        if deleted is not None:
            await self.run_store.redact_runs_for_session(
                session_id=chat_id,
                user_id=user_id,
                agent_id=chat.agent_id,
            )
            if self.attachment_service is not None:
                await self.attachment_service.delete_chat(
                    user_id=user_id,
                    chat_id=chat_id,
                )
            await self._append_runtime_event(
                context=event_context(deleted),
                kind=AgentEventKind.CHAT_DELETED,
                payload={},
            )
        return deleted

    async def prepare_stream_message(
        self,
        *,
        user_id: str,
        chat_id: str,
        text: str,
        attachment_ids: tuple[str, ...] = (),
        client_turn_id: str | None = None,
        current_location: ClientLocation | None = None,
        communication_medium: CommunicationMedium = CommunicationMedium.WEB_CHAT,
    ) -> PreparedTextChatTurn:
        chat = await self.runtime_store.get_session(
            session_id=chat_id,
            user_id=user_id,
            channel=channel_for_communication_medium(communication_medium),
        )
        if chat is None or chat.agent_id is None:
            message = "Chat was not found"
            raise LookupError(message)

        stripped_text = text.strip()
        if not stripped_text and not attachment_ids:
            message = "Message text or at least one image is required"
            raise ValueError(message)
        provider = TextProvider(chat.provider)
        resolved_model = chat.model
        if attachment_ids:
            routed_model = text_model_for_image_input(provider, chat.model)
            if routed_model is None:
                editing_available = self.image_editing_available is not None and await self.image_editing_available(
                    user_id
                )
                if not editing_available:
                    message = "The selected model does not support image input"
                    raise AttachmentModelUnsupportedError(message)
            else:
                resolved_model = routed_model
        if attachment_ids and self.attachment_service is None:
            message = "Chat image attachments are not configured"
            raise AttachmentValidationError(message)
        if self.attachment_service is not None:
            if client_turn_id is not None:
                existing_turn = await self.attachment_service.existing_turn(
                    user_id=user_id,
                    chat_id=chat_id,
                    text=stripped_text,
                    attachment_ids=attachment_ids,
                    client_turn_id=client_turn_id,
                )
                if existing_turn is not None:
                    return PreparedTextChatTurn(
                        chat=chat,
                        text=stripped_text,
                        provider=provider,
                        model=resolved_model,
                        current_location=current_location,
                        duplicate=True,
                    )
            if attachment_ids:
                await self.attachment_service.validate_ready_set(
                    user_id=user_id,
                    chat_id=chat_id,
                    attachment_ids=attachment_ids,
                )
        return PreparedTextChatTurn(
            chat=chat,
            text=stripped_text,
            provider=provider,
            model=resolved_model,
            current_location=current_location,
        )

    async def stream_message(
        self,
        *,
        user_id: str,
        chat_id: str,
        text: str,
        attachment_ids: tuple[str, ...] = (),
        client_turn_id: str | None = None,
        current_location: ClientLocation | None = None,
        run_id: str | None = None,
        manage_run: bool = True,
        communication_medium: CommunicationMedium = CommunicationMedium.WEB_CHAT,
        append_user_message: bool = True,
        persist_assistant_message: bool = True,
        user_message_source: AgentTranscriptSource = AgentTranscriptSource.TEXT_INPUT,
        assistant_message_source: AgentTranscriptSource = AgentTranscriptSource.TEXT_OUTPUT,
        prepared_turn: PreparedTextChatTurn | None = None,
    ) -> AsyncIterator[AgentEvent]:
        turn = prepared_turn or await self.prepare_stream_message(
            user_id=user_id,
            chat_id=chat_id,
            text=text,
            attachment_ids=attachment_ids,
            client_turn_id=client_turn_id,
            current_location=current_location,
            communication_medium=communication_medium,
        )
        chat = turn.chat
        if chat.id != chat_id or chat.user_id != user_id:
            message = "Prepared chat turn does not match the authenticated request"
            raise PermissionError(message)
        if chat.agent_id is None:
            message = "Chat was not found"
            raise LookupError(message)
        agent_id = chat.agent_id
        if turn.duplicate:
            return
        stripped_text = turn.text
        provider = turn.provider
        resolved_model = turn.model

        startup_started = time.perf_counter()
        run_preferences_started = startup_started
        run, preferences = await asyncio.gather(
            self._get_or_start_run(
                chat=chat,
                text=stripped_text,
                attachment_count=len(attachment_ids),
                model=resolved_model,
                client_turn_id=client_turn_id,
                run_id=run_id,
            ),
            self._preferences(chat.user_id),
        )
        run_preferences_finished = time.perf_counter()
        persisted_model = run.payload_json.get("model")
        if (
            isinstance(persisted_model, str)
            and persisted_model
            and (not attachment_ids or text_model_supports_image_input(provider, persisted_model))
        ):
            resolved_model = persisted_model
        context_setup_started = time.perf_counter()
        context_task = asyncio.create_task(
            self._run_context(
                chat,
                run=run,
                preferences=preferences,
                model=resolved_model,
                has_images=bool(attachment_ids),
                task_query=stripped_text or None,
                current_location=turn.current_location,
            )
        )
        run_setup_task = asyncio.create_task(
            self._run_setup(
                user_id=chat.user_id,
                provider=provider,
                preferences=preferences,
            )
        )
        try:
            context = await context_task
        except BaseException:
            run_setup_task.cancel()
            await asyncio.gather(run_setup_task, return_exceptions=True)
            raise
        reasoning_mode = text_reasoning_mode_for_run_kind(run.kind)
        generated_artifacts: list[AgentToolAttachmentArtifact] = []
        place_card_batches: list[tuple[PlaceCardArtifact, ...]] = []
        visible_assistant_parts: list[str] = []
        try:
            persistence_started = time.perf_counter()
            if append_user_message:
                created = await self._append_user_message(
                    context=context,
                    text=stripped_text,
                    attachment_ids=attachment_ids,
                    client_turn_id=client_turn_id,
                    source=user_message_source,
                    owner_authenticated=run.kind == AgentRunKind.TEXT_CHAT_TURN.value,
                )
                if not created:
                    if manage_run and context.run_id is not None:
                        await self.run_store.complete_run(
                            run_id=context.run_id,
                            user_id=context.user_id,
                            result_json={"duplicate_turn": True},
                        )
                    return
            if append_user_message and should_auto_title(chat):
                await self.runtime_store.update_session_title(
                    session_id=chat.id,
                    user_id=user_id,
                    agent_id=agent_id,
                    title=title_from_message(stripped_text) if stripped_text else "Image",
                )
            persistence_finished = time.perf_counter()
            run_setup = await run_setup_task
            context_setup_finished = time.perf_counter()
            await self._raise_if_cancel_requested(context)
        except (asyncio.CancelledError, GeneratorExit):
            if manage_run:
                await self._finalize_disconnected_run(context)
            raise
        except AgentRunCancelledError:
            yield await self._mark_run_cancelled(context=context)
            if run_id is not None:
                raise
            return
        except Exception as exc:
            self._log_run_failure(
                exc=exc,
                user_id=user_id,
                agent_id=agent_id,
                chat_id=chat_id,
                provider=context.provider,
                model=context.model,
            )
            yield await self._mark_run_failed(
                context=context,
                exc=exc,
                manage_run=manage_run,
            )
            if run_id is not None:
                raise
            return
        finally:
            if not run_setup_task.done():
                run_setup_task.cancel()
            await asyncio.gather(run_setup_task, return_exceptions=True)

        try:
            run_started_event = await self._append_runtime_event(
                context=context,
                kind=AgentEventKind.RUN_STARTED,
                payload={
                    "provider": context.provider,
                    "model": context.model,
                    "reasoning_mode": reasoning_mode.value,
                },
            )
            first_event_ready = time.perf_counter()
            record_text_timing(
                "VoxosTextTurnStartup",
                {
                    "provider": context.provider,
                    "model": context.model,
                    "run_and_preferences_ms": ((run_preferences_finished - run_preferences_started) * 1000),
                    "context_and_credentials_ms": ((context_setup_finished - context_setup_started) * 1000),
                    "persistence_ms": ((persistence_finished - persistence_started) * 1000),
                    "total_ms": ((first_event_ready - startup_started) * 1000),
                },
            )
            yield run_started_event
            async for event in self._stream_provider_loop(
                context,
                run_setup,
                reasoning_mode=reasoning_mode,
                manage_run=manage_run,
                persist_assistant_message=persist_assistant_message,
                assistant_message_source=assistant_message_source,
                generated_artifacts=generated_artifacts,
                place_card_batches=place_card_batches,
                visible_assistant_parts=visible_assistant_parts,
            ):
                yield event
        except (asyncio.CancelledError, GeneratorExit):
            if manage_run:
                await self._finalize_disconnected_run(context)
            raise
        except AgentRunCancelledError:
            yield await self._mark_run_cancelled(context=context)
            if run_id is not None:
                raise
        except Exception as exc:
            partial_text = "".join(visible_assistant_parts)
            if persist_assistant_message and (partial_text.strip() or generated_artifacts):
                try:
                    await self._append_assistant_message(
                        context=context,
                        text=partial_text if partial_text.strip() else GENERATED_IMAGE_RECOVERY_TEXT,
                        source=assistant_message_source,
                        attachment_artifacts=tuple(generated_artifacts),
                        interrupted=True,
                    )
                    generated_artifacts.clear()
                except Exception:
                    logger.exception(
                        "failed to preserve partial assistant response after text response failure",
                    )
            self._log_run_failure(
                exc=exc,
                user_id=user_id,
                agent_id=agent_id,
                chat_id=chat_id,
                provider=context.provider,
                model=context.model,
            )
            yield await self._mark_run_failed(
                context=context,
                exc=exc,
                manage_run=manage_run,
            )
            if run_id is not None:
                raise
            return

    async def _stream_provider_loop(
        self,
        context: AgentRunContext,
        run_setup: TextRunSetup,
        *,
        reasoning_mode: TextReasoningMode,
        manage_run: bool,
        persist_assistant_message: bool,
        assistant_message_source: AgentTranscriptSource,
        generated_artifacts: list[AgentToolAttachmentArtifact],
        place_card_batches: list[tuple[PlaceCardArtifact, ...]],
        visible_assistant_parts: list[str],
    ) -> AsyncIterator[AgentEvent]:
        preferences = run_setup.preferences
        credentials = run_setup.credentials
        profile = response_profile(context.communication_medium)
        preparation_started = time.perf_counter()
        history_started = time.perf_counter()
        prepared_catalog, transcript_records = await asyncio.gather(
            self._prepare_tool_catalog(context),
            self.runtime_store.list_transcript_items(
                session_id=context.session_id,
                user_id=context.user_id,
                agent_id=context.agent_id,
                limit=profile.max_recent_messages,
            ),
        )
        history_loaded = time.perf_counter()
        catalog_tool_provider = prepared_catalog.provider
        tools = prepared_catalog.tools
        messages = await self._provider_messages(
            context,
            tools=tools,
            capability_directory=catalog_tool_provider.deferred_capability_directory(),
            limit=profile.max_recent_messages,
            character_limit=profile.max_history_characters,
            transcript_records=transcript_records,
        )
        messages_prepared = time.perf_counter()
        record_text_timing(
            "VoxosTextChatPreparation",
            {
                "provider": context.provider,
                "model": context.model,
                "tool_provider_ms": prepared_catalog.tool_provider_ms,
                "tool_catalog_ms": prepared_catalog.tool_catalog_ms,
                "history_load_ms": ((history_loaded - history_started) * 1000),
                "message_build_ms": ((messages_prepared - history_loaded) * 1000),
                "total_ms": (messages_prepared - preparation_started) * 1000,
                "tool_count": len(tools),
                "message_count": len(messages),
            },
        )
        record_provider_image_count(
            sum(
                isinstance(part, ProviderInputImage)
                for message in messages
                if not isinstance(message.content, str)
                for part in message.content
            ),
        )
        provider = TextProvider(context.provider)
        discovery_tool_names = TOOL_DISCOVERY_NAMES if catalog_tool_provider.search_enabled else frozenset()
        continuation_items: list[JsonObject] = []
        rejected_tool_call_fingerprints: set[str] = set()
        tool_round = 0
        execution_tool_rounds = 0
        discovery_tool_rounds = 0
        empty_response_retries = 0
        tool_protocol_repair_retries = 0
        effective_reasoning_mode = reasoning_mode
        tool_budget_notice_sent = False

        while tool_round <= MAX_TOOL_ROUNDS + MAX_TOOL_DISCOVERY_ROUNDS:
            tools_enabled = execution_tool_rounds < MAX_TOOL_ROUNDS
            if not tools_enabled:
                messages = [message for message in messages if not message.discard_when_tools_unavailable]
            if not tools_enabled and not tool_budget_notice_sent:
                messages.append(
                    ProviderMessage(
                        role="system",
                        content=TOOL_BUDGET_EXHAUSTED_PROMPT,
                    ),
                )
                tool_budget_notice_sent = True
            assistant_parts: list[str] = []
            response_visible_start = len(visible_assistant_parts)
            tool_calls: tuple[TextProviderToolCall, ...] = ()
            response_continuation_items: tuple[JsonObject, ...] = ()
            response_summary = TextProviderResponseSummary()
            output_filter = ToolProtocolArtifactFilter(
                frozenset(tool.name for tool in tools),
            )
            required_tools = tuple(tool for tool in tools if tool.required) if tools_enabled and tool_round == 0 else ()
            if len(required_tools) > 1:
                message = "A run cannot require more than one exact tool"
                raise RuntimeError(message)
            ordinary_tools = tuple(
                tool
                for tool in tools
                if not tool.required
                and (discovery_tool_rounds < MAX_TOOL_DISCOVERY_ROUNDS or tool.name not in discovery_tool_names)
            )
            request_tools = required_tools or (ordinary_tools if tools_enabled else ())
            tool_choice = TextToolChoice.REQUIRED if required_tools else TextToolChoice.AUTO
            await self._raise_if_cancel_requested(context)
            provider_attempt = 1
            while True:
                provider_stream = self.provider_client.stream_chat(
                    TextProviderRequest(
                        provider=provider,
                        model=context.model,
                        credentials=None if credentials is None else credentials.token,
                        config=preferences.provider_config,
                        messages=tuple(messages),
                        continuation_items=tuple(continuation_items),
                        tools=request_tools,
                        max_output_tokens=profile.max_output_tokens,
                        reasoning_mode=effective_reasoning_mode,
                        tool_choice=tool_choice,
                    ),
                ).__aiter__()
                provider_started = time.perf_counter()
                provider_wait_ms = 0.0
                first_event_wait_ms: float | None = None
                first_delta_wait_ms: float | None = None
                provider_event_count = 0
                delta_event_count = 0
                delta_character_count = 0
                provider_status = "failed"
                transport_error_kind: str | None = None
                retry_scheduled = False
                try:
                    while True:
                        provider_wait_started = time.perf_counter()
                        try:
                            provider_event = await anext(provider_stream)
                        except StopAsyncIteration:
                            provider_status = "completed"
                            break
                        finally:
                            provider_wait_ms += (time.perf_counter() - provider_wait_started) * 1000
                        provider_event_count += 1
                        if first_event_wait_ms is None:
                            first_event_wait_ms = provider_wait_ms
                        await self._raise_if_cancel_requested(context)
                        if provider_event.kind == TextProviderEventKind.MESSAGE_DELTA:
                            if first_delta_wait_ms is None:
                                first_delta_wait_ms = provider_wait_ms
                            delta = output_filter.feed(provider_event.delta or "")
                            if not delta:
                                continue
                            delta_event_count += 1
                            delta_character_count += len(delta)
                            assistant_parts.append(delta)
                            if required_tools:
                                continue
                            visible_assistant_parts.append(delta)
                            yield self._runtime_event(
                                context=context,
                                kind=AgentEventKind.MESSAGE_DELTA,
                                payload={"delta": delta},
                            )
                            continue
                        if provider_event.kind == TextProviderEventKind.USAGE:
                            yield await self._append_usage(context, provider_event.usage)
                            continue
                        if provider_event.kind == TextProviderEventKind.TOOL_CALLS:
                            tool_calls = provider_event.tool_calls
                            response_continuation_items = provider_event.continuation_items
                            continue
                        if provider_event.kind == TextProviderEventKind.RESPONSE_SUMMARY:
                            response_summary = provider_event.response_summary or response_summary
                except TextProviderTransportError as exc:
                    transport_error_kind = exc.kind.value
                    # Replaying after any accepted event could duplicate visible output,
                    # persisted usage, or model tool intent.
                    retry_scheduled = provider_event_count == 0 and provider_attempt <= MAX_TRANSIENT_PROVIDER_RETRIES
                    if retry_scheduled:
                        provider_status = "retrying"
                    else:
                        raise
                except (asyncio.CancelledError, GeneratorExit):
                    provider_status = "cancelled"
                    raise
                finally:
                    record_text_timing(
                        "VoxosTextProviderStream",
                        {
                            "provider": context.provider,
                            "model": context.model,
                            "tool_round": tool_round,
                            "execution_tool_rounds": execution_tool_rounds,
                            "discovery_tool_rounds": discovery_tool_rounds,
                            "attempt": provider_attempt,
                            "status": provider_status,
                            "retry_scheduled": retry_scheduled,
                            "transport_error_kind": transport_error_kind,
                            "tool_count": len(request_tools),
                            "message_count": len(messages),
                            "provider_event_count": provider_event_count,
                            "delta_event_count": delta_event_count,
                            "delta_character_count": delta_character_count,
                            "first_event_wait_ms": first_event_wait_ms,
                            "first_delta_wait_ms": first_delta_wait_ms,
                            "provider_wait_ms": provider_wait_ms,
                            "wall_ms": (time.perf_counter() - provider_started) * 1000,
                        },
                    )
                if not retry_scheduled:
                    break
                await asyncio.sleep(
                    TRANSIENT_PROVIDER_RETRY_DELAY_SECONDS * provider_attempt,
                )
                provider_attempt += 1

            assistant_text = "".join(assistant_parts)
            if tool_calls:
                if not tools_enabled:
                    message = "Maximum tool call depth was exceeded"
                    raise RuntimeError(message)
                if required_tools and (len(tool_calls) != 1 or tool_calls[0].name != required_tools[0].name):
                    raise TextProviderToolProtocolError
                has_discovery_calls = any(call.name in discovery_tool_names for call in tool_calls)
                has_execution_calls = any(call.name not in discovery_tool_names for call in tool_calls)
                if has_discovery_calls and discovery_tool_rounds >= MAX_TOOL_DISCOVERY_ROUNDS:
                    raise TextProviderToolProtocolError
                continuation_items.extend(
                    response_continuation_items or provider_function_call_items(provider, tool_calls),
                )
                async for event in self._execute_tool_calls(
                    context=context,
                    provider=provider,
                    tool_provider=catalog_tool_provider,
                    messages=messages,
                    tools=request_tools,
                    tool_calls=tool_calls,
                    continuation_items=continuation_items,
                    rejected_call_fingerprints=rejected_tool_call_fingerprints,
                    generated_artifacts=generated_artifacts,
                    place_card_batches=place_card_batches,
                ):
                    yield event
                tool_round += 1
                discovery_tool_rounds += int(has_discovery_calls)
                execution_tool_rounds += int(has_execution_calls)
                continue

            if required_tools:
                raise TextProviderToolProtocolError

            final_delta = output_filter.flush()
            if final_delta:
                assistant_parts.append(final_delta)
                assistant_text += final_delta
                visible_assistant_parts.append(final_delta)
                yield self._runtime_event(
                    context=context,
                    kind=AgentEventKind.MESSAGE_DELTA,
                    payload={"delta": final_delta},
                )
            if output_filter.removed_tool_call_artifact:
                if (
                    not tools_enabled
                    and not assistant_text.strip()
                    and tool_protocol_repair_retries < MAX_TOOL_PROTOCOL_REPAIR_RETRIES
                ):
                    tool_protocol_repair_retries += 1
                    del visible_assistant_parts[response_visible_start:]
                    effective_reasoning_mode = TextReasoningMode.FAST
                    messages.append(
                        ProviderMessage(
                            role="system",
                            content=TOOL_PROTOCOL_REPAIR_PROMPT,
                        ),
                    )
                    continue
                raise TextProviderToolProtocolError
            if not assistant_text.strip():
                if empty_response_retries >= MAX_EMPTY_PROVIDER_RESPONSE_RETRIES:
                    raise TextProviderEmptyFinalResponseError(response_summary)
                empty_response_retries += 1
                del visible_assistant_parts[response_visible_start:]
                if response_summary.saw_reasoning_content:
                    # A reasoning-only response is not useful to the caller. Retry in
                    # the provider's fast/instruct mode so the recovery attempt changes
                    # the failed condition instead of asking for the same output again.
                    effective_reasoning_mode = TextReasoningMode.FAST
                messages.append(
                    ProviderMessage(
                        role="system",
                        content=EMPTY_PROVIDER_RESPONSE_REPAIR_PROMPT,
                        discard_when_tools_unavailable=True,
                    ),
                )
                continue

            completed_place_cards = selected_place_cards_for_message(
                place_card_batches,
                assistant_text=assistant_text,
            )
            assistant_message_id: str | None = None
            visible_assistant_text = "".join(visible_assistant_parts)
            if persist_assistant_message and assistant_text.strip():
                assistant_message = await self._append_assistant_message(
                    context=context,
                    text=visible_assistant_text,
                    source=assistant_message_source,
                    attachment_artifacts=tuple(generated_artifacts),
                )
                assistant_message_id = assistant_message.id
                generated_artifacts.clear()
                visible_assistant_parts.clear()
            completion_payload: JsonObject = {
                "text": visible_assistant_text,
                "place_cards": [card.model_dump(mode="json") for card in completed_place_cards],
            }
            if assistant_message_id is not None:
                completion_payload["message_id"] = assistant_message_id
            yield await self._append_runtime_event(
                context=context,
                kind=AgentEventKind.MESSAGE_COMPLETED,
                payload=completion_payload,
                persisted_payload={"text": visible_assistant_text},
            )
            if manage_run and context.run_id is not None:
                await self.run_store.complete_run(
                    run_id=context.run_id,
                    user_id=context.user_id,
                    result_json={"characters_count": len(visible_assistant_text)},
                )
            if manage_run:
                yield await self._append_runtime_event(
                    context=context,
                    kind=AgentEventKind.RUN_COMPLETED,
                    payload={},
                )
            return

        message = "Text provider loop ended without a completed response"
        raise RuntimeError(message)

    async def _execute_tool_calls(
        self,
        *,
        context: AgentRunContext,
        provider: TextProvider = TextProvider.OPENAI,
        tool_provider: CoreAgentToolProvider | None = None,
        messages: list[ProviderMessage],
        tools: tuple[AgentTool, ...],
        tool_calls: tuple[TextProviderToolCall, ...],
        continuation_items: list[JsonObject] | None = None,
        rejected_call_fingerprints: set[str] | None = None,
        generated_artifacts: list[AgentToolAttachmentArtifact] | None = None,
        place_card_batches: list[tuple[PlaceCardArtifact, ...]] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        tool_provider = tool_provider or self.tool_provider
        tools_by_name = {tool.name: tool for tool in tools}
        invalid_tool_names: set[str] = set()
        rejected_fingerprints = rejected_call_fingerprints if rejected_call_fingerprints is not None else set()
        parallel_calls: list[PreparedToolCall] = []
        for tool_call in tool_calls:
            await self._raise_if_cancel_requested(context)
            fingerprint = tool_call_fingerprint(tool_call)
            if tool_call.name in invalid_tool_names or fingerprint in rejected_fingerprints:
                async for event in self._execute_parallel_tool_calls(
                    context=context,
                    provider=provider,
                    tool_provider=tool_provider,
                    messages=messages,
                    continuation_items=continuation_items,
                    prepared_calls=tuple(parallel_calls),
                    invalid_tool_names=invalid_tool_names,
                    rejected_fingerprints=rejected_fingerprints,
                    generated_artifacts=generated_artifacts,
                    place_card_batches=place_card_batches,
                ):
                    yield event
                parallel_calls.clear()
                append_tool_call_output(
                    messages=messages,
                    continuation_items=continuation_items,
                    provider=provider,
                    call_id=tool_call.call_id,
                    output=(
                        "This call was not executed because the same tool already had "
                        "invalid arguments in this response, or this exact request was "
                        "already rejected. Correct one call before batching more requests."
                    ),
                )
                invalid_tool_names.add(tool_call.name)
                continue
            tool = tools_by_name.get(tool_call.name)
            try:
                accepted_arguments = validate_tool_call_arguments(
                    tool,
                    tool_call.arguments_json,
                )
            except ToolArgumentSchemaError as exc:
                async for event in self._execute_parallel_tool_calls(
                    context=context,
                    provider=provider,
                    tool_provider=tool_provider,
                    messages=messages,
                    continuation_items=continuation_items,
                    prepared_calls=tuple(parallel_calls),
                    invalid_tool_names=invalid_tool_names,
                    rejected_fingerprints=rejected_fingerprints,
                    generated_artifacts=generated_artifacts,
                    place_card_batches=place_card_batches,
                ):
                    yield event
                parallel_calls.clear()
                invalid_tool_names.add(tool_call.name)
                rejected_fingerprints.add(fingerprint)
                yield await self._record_tool_call_failure(
                    context=context,
                    tool_call=tool_call,
                    exc=exc,
                )
                repair_output = (
                    f"{exc}. The tool was not executed. Review its JSON schema "
                    "and call it again with corrected arguments."
                )
                append_tool_call_output(
                    messages=messages,
                    continuation_items=continuation_items,
                    provider=provider,
                    call_id=tool_call.call_id,
                    output=repair_output,
                )
                continue
            except (ToolExecutionSecurityError, ToolSchemaDefinitionError) as exc:
                async for event in self._execute_parallel_tool_calls(
                    context=context,
                    provider=provider,
                    tool_provider=tool_provider,
                    messages=messages,
                    continuation_items=continuation_items,
                    prepared_calls=tuple(parallel_calls),
                    invalid_tool_names=invalid_tool_names,
                    rejected_fingerprints=rejected_fingerprints,
                    generated_artifacts=generated_artifacts,
                    place_card_batches=place_card_batches,
                ):
                    yield event
                parallel_calls.clear()
                if tool_call.name != TOOL_CALL_NAME:
                    invalid_tool_names.add(tool_call.name)
                rejected_fingerprints.add(fingerprint)
                yield await self._record_tool_call_failure(
                    context=context,
                    tool_call=tool_call,
                    exc=exc,
                )
                append_tool_call_output(
                    messages=messages,
                    continuation_items=continuation_items,
                    provider=provider,
                    call_id=tool_call.call_id,
                    output=tool_failure_recovery_output(str(exc)),
                )
                continue
            accepted_tool_call = tool_call.model_copy(
                update={"arguments_json": accepted_arguments},
            )
            fingerprint = tool_call_fingerprint(accepted_tool_call)
            activity_tool = (
                tool_provider.activity_tool_for_call(
                    tool_name=tool_call.name,
                    arguments_json=accepted_arguments,
                )
                if isinstance(tool_provider, RunScopedToolCatalogProvider)
                else None
            )
            preparation_error = (
                ToolNotExposedError(
                    f"Tool is not exposed in this request: {tool_call.name}",
                )
                if tool is None and isinstance(tool_provider, RunScopedToolCatalogProvider) and activity_tool is None
                else None
            )
            prepared_call = PreparedToolCall(
                tool_call=accepted_tool_call,
                fingerprint=fingerprint,
                tool=tool,
                activity_tool=activity_tool,
                preparation_error=preparation_error,
            )
            if tool is not None and tool.parallel_safe:
                parallel_calls.append(prepared_call)
                continue

            async for event in self._execute_parallel_tool_calls(
                context=context,
                provider=provider,
                tool_provider=tool_provider,
                messages=messages,
                continuation_items=continuation_items,
                prepared_calls=tuple(parallel_calls),
                invalid_tool_names=invalid_tool_names,
                rejected_fingerprints=rejected_fingerprints,
                generated_artifacts=generated_artifacts,
                place_card_batches=place_card_batches,
            ):
                yield event
            parallel_calls.clear()
            async for event in self._execute_tool_call_batch(
                context=context,
                provider=provider,
                tool_provider=tool_provider,
                messages=messages,
                continuation_items=continuation_items,
                prepared_calls=(prepared_call,),
                invalid_tool_names=invalid_tool_names,
                rejected_fingerprints=rejected_fingerprints,
                generated_artifacts=generated_artifacts,
                place_card_batches=place_card_batches,
            ):
                yield event

        async for event in self._execute_parallel_tool_calls(
            context=context,
            provider=provider,
            tool_provider=tool_provider,
            messages=messages,
            continuation_items=continuation_items,
            prepared_calls=tuple(parallel_calls),
            invalid_tool_names=invalid_tool_names,
            rejected_fingerprints=rejected_fingerprints,
            generated_artifacts=generated_artifacts,
            place_card_batches=place_card_batches,
        ):
            yield event

    async def _execute_parallel_tool_calls(
        self,
        *,
        context: AgentRunContext,
        provider: TextProvider,
        tool_provider: CoreAgentToolProvider,
        messages: list[ProviderMessage],
        continuation_items: list[JsonObject] | None,
        prepared_calls: tuple[PreparedToolCall, ...],
        invalid_tool_names: set[str],
        rejected_fingerprints: set[str],
        generated_artifacts: list[AgentToolAttachmentArtifact] | None,
        place_card_batches: list[tuple[PlaceCardArtifact, ...]] | None,
    ) -> AsyncIterator[AgentEvent]:
        for pending_batch in parallel_tool_call_batches(prepared_calls):
            batch: list[PreparedToolCall] = []
            for prepared_call in pending_batch:
                tool_call = prepared_call.tool_call
                if tool_call.name in invalid_tool_names or prepared_call.fingerprint in rejected_fingerprints:
                    append_tool_call_output(
                        messages=messages,
                        continuation_items=continuation_items,
                        provider=provider,
                        call_id=tool_call.call_id,
                        output=(
                            "This call was not executed because the same tool already had "
                            "invalid arguments in this response, or this exact request was "
                            "already rejected. Correct one call before batching more requests."
                        ),
                    )
                    invalid_tool_names.add(tool_call.name)
                    continue
                batch.append(prepared_call)

            async for event in self._execute_tool_call_batch(
                context=context,
                provider=provider,
                tool_provider=tool_provider,
                messages=messages,
                continuation_items=continuation_items,
                prepared_calls=tuple(batch),
                invalid_tool_names=invalid_tool_names,
                rejected_fingerprints=rejected_fingerprints,
                generated_artifacts=generated_artifacts,
                place_card_batches=place_card_batches,
            ):
                yield event

    async def _execute_tool_call_batch(
        self,
        *,
        context: AgentRunContext,
        provider: TextProvider,
        tool_provider: CoreAgentToolProvider,
        messages: list[ProviderMessage],
        continuation_items: list[JsonObject] | None,
        prepared_calls: tuple[PreparedToolCall, ...],
        invalid_tool_names: set[str],
        rejected_fingerprints: set[str],
        generated_artifacts: list[AgentToolAttachmentArtifact] | None,
        place_card_batches: list[tuple[PlaceCardArtifact, ...]] | None,
    ) -> AsyncIterator[AgentEvent]:
        if not prepared_calls:
            return

        await self._raise_if_cancel_requested(context)
        for prepared_call in prepared_calls:
            tool_call = prepared_call.tool_call
            yield await self._append_runtime_event(
                context=context,
                kind=AgentEventKind.TOOL_CALL_STARTED,
                payload={
                    "call_id": tool_call.call_id,
                    "name": tool_call.name,
                    "activity": tool_activity_payload(prepared_call),
                },
            )

        outcomes = await run_tool_call_batch(
            tool_provider=tool_provider,
            context=context,
            prepared_calls=prepared_calls,
        )
        for outcome in outcomes:
            tool_call = outcome.prepared_call.tool_call
            if isinstance(outcome.error, ToolArgumentSchemaError):
                if tool_call.name != TOOL_CALL_NAME:
                    invalid_tool_names.add(tool_call.name)
                rejected_fingerprints.add(outcome.prepared_call.fingerprint)
                yield await self._record_tool_call_failure(
                    context=context,
                    tool_call=tool_call,
                    exc=outcome.error,
                )
                append_tool_call_output(
                    messages=messages,
                    continuation_items=continuation_items,
                    provider=provider,
                    call_id=tool_call.call_id,
                    output=(
                        f"{outcome.error}. The target tool was not executed. "
                        "Use tool_describe and retry with corrected arguments."
                    ),
                )
                continue
            if isinstance(outcome.error, ToolNotExposedError):
                invalid_tool_names.add(tool_call.name)
                rejected_fingerprints.add(outcome.prepared_call.fingerprint)
                yield await self._record_tool_call_failure(
                    context=context,
                    tool_call=tool_call,
                    exc=outcome.error,
                )
                append_tool_call_output(
                    messages=messages,
                    continuation_items=continuation_items,
                    provider=provider,
                    call_id=tool_call.call_id,
                    output=(
                        "That tool name is not available in this run. Use exactly one "
                        "of the listed tool names, or answer without calling a tool."
                    ),
                )
                continue
            if isinstance(outcome.error, McpToolCallRejectedError):
                if tool_call.name != TOOL_CALL_NAME:
                    invalid_tool_names.add(tool_call.name)
                rejected_fingerprints.add(outcome.prepared_call.fingerprint)
                yield await self._record_tool_call_failure(
                    context=context,
                    tool_call=tool_call,
                    exc=outcome.error,
                )
                append_tool_call_output(
                    messages=messages,
                    continuation_items=continuation_items,
                    provider=provider,
                    call_id=tool_call.call_id,
                    output=(
                        f"{outcome.error}. "
                        + (
                            outcome.error.recovery_guidance
                            or "Review the tool's JSON schema and call it again with corrected arguments."
                        )
                    ),
                )
                continue
            if isinstance(outcome.error, McpConnectionTestError):
                yield await self._record_tool_call_failure(
                    context=context,
                    tool_call=tool_call,
                    exc=outcome.error,
                )
                append_tool_call_output(
                    messages=messages,
                    continuation_items=continuation_items,
                    provider=provider,
                    call_id=tool_call.call_id,
                    output=(
                        f"{outcome.error}. Continue with any successful tool results. "
                        "If this result is required for an accurate answer, tell the user "
                        "what could not be verified and that the tool is temporarily unavailable."
                    ),
                )
                continue
            if outcome.error is not None:
                if tool_call.name != TOOL_CALL_NAME:
                    invalid_tool_names.add(tool_call.name)
                rejected_fingerprints.add(outcome.prepared_call.fingerprint)
                yield await self._record_tool_call_failure(
                    context=context,
                    tool_call=tool_call,
                    exc=outcome.error,
                )
                append_tool_call_output(
                    messages=messages,
                    continuation_items=continuation_items,
                    provider=provider,
                    call_id=tool_call.call_id,
                    output=tool_failure_recovery_output(str(outcome.error)),
                )
                continue

            result = outcome.result
            if result is None:
                message = "Tool call completed without a result or error"
                raise RuntimeError(message)
            persisted_output_text = (
                result.output_text if result.persisted_output_text is None else result.persisted_output_text
            )
            await self.runtime_store.append_tool_event(
                AgentToolEvent(
                    session_id=context.session_id,
                    user_id=context.user_id,
                    agent_id=context.agent_id,
                    call_id=result.call_id,
                    name=result.name,
                    arguments_json=(
                        tool_call.arguments_json
                        if result.executed_arguments_json is None
                        else result.executed_arguments_json
                    ),
                    output_text=persisted_output_text,
                    is_error=result.is_error,
                    created_at=utc_now(),
                ),
            )
            if result.is_error:
                if tool_call.name != TOOL_CALL_NAME:
                    invalid_tool_names.add(tool_call.name)
                rejected_fingerprints.add(outcome.prepared_call.fingerprint)
                persisted_error_message = tool_result_error_message(
                    persisted_output_text,
                )
                append_tool_call_output(
                    messages=messages,
                    continuation_items=continuation_items,
                    provider=provider,
                    call_id=result.call_id,
                    output=tool_failure_recovery_output(
                        tool_result_error_message(result.output_text),
                    ),
                )
                yield await self._append_runtime_event(
                    context=context,
                    kind=AgentEventKind.TOOL_CALL_FAILED,
                    payload={
                        "call_id": result.call_id,
                        "name": result.name,
                        "error_type": "ToolResultError",
                        "error_message": tool_result_error_message(
                            result.output_text,
                        ),
                        "output": result.output_text,
                        "activity": tool_activity_payload(outcome.prepared_call),
                    },
                    persisted_payload={
                        "call_id": result.call_id,
                        "name": result.name,
                        "error_type": "ToolResultError",
                        "error_message": persisted_error_message,
                        "output": persisted_output_text,
                        "activity": tool_activity_payload(outcome.prepared_call),
                    },
                )
                continue
            append_tool_call_output(
                messages=messages,
                continuation_items=continuation_items,
                provider=provider,
                call_id=result.call_id,
                output=result.output_text,
            )
            if generated_artifacts is not None:
                generated_artifacts.extend(result.attachment_artifacts)
            if place_card_batches is not None and result.place_card_artifacts:
                place_card_batches.append(result.place_card_artifacts)
            yield await self._append_runtime_event(
                context=context,
                kind=AgentEventKind.TOOL_CALL_COMPLETED,
                payload={
                    "call_id": result.call_id,
                    "name": result.name,
                    "output": result.output_text,
                    "is_error": result.is_error,
                    "attachments": [artifact.model_dump(mode="json") for artifact in result.attachment_artifacts],
                    "activity": tool_activity_payload(outcome.prepared_call),
                },
                persisted_payload={
                    "call_id": result.call_id,
                    "name": result.name,
                    "output": persisted_output_text,
                    "is_error": result.is_error,
                    "attachments": [artifact.model_dump(mode="json") for artifact in result.attachment_artifacts],
                    "activity": tool_activity_payload(outcome.prepared_call),
                },
            )

    async def _record_tool_call_failure(
        self,
        *,
        context: AgentRunContext,
        tool_call: TextProviderToolCall,
        exc: Exception,
    ) -> AgentEvent:
        await self.runtime_store.append_tool_event(
            AgentToolEvent(
                session_id=context.session_id,
                user_id=context.user_id,
                agent_id=context.agent_id,
                call_id=tool_call.call_id,
                name=tool_call.name,
                arguments_json=tool_call.arguments_json,
                output_text=str(exc),
                is_error=True,
                created_at=utc_now(),
            ),
        )
        return await self._append_runtime_event(
            context=context,
            kind=AgentEventKind.TOOL_CALL_FAILED,
            payload={
                "call_id": tool_call.call_id,
                "name": tool_call.name,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            },
        )

    async def _prepare_tool_catalog(
        self,
        context: AgentRunContext,
    ) -> PreparedTextToolCatalog:
        preparation_started = time.perf_counter()
        prepared_tool_provider = await prepare_agent_tool_provider(
            self.tool_provider,
            context,
        )
        tool_provider_prepared = time.perf_counter()
        catalog_tool_provider = await RunScopedToolCatalogProvider.create(
            provider=prepared_tool_provider,
            context=context,
        )
        tools = await catalog_tool_provider.list_tools(context)
        catalog_created = time.perf_counter()
        return PreparedTextToolCatalog(
            provider=catalog_tool_provider,
            tools=tools,
            tool_provider_ms=((tool_provider_prepared - preparation_started) * 1000),
            tool_catalog_ms=((catalog_created - tool_provider_prepared) * 1000),
        )

    async def _provider_messages(
        self,
        context: AgentRunContext,
        *,
        tools: tuple[AgentTool, ...] = (),
        capability_directory: str | None = None,
        limit: int | None = None,
        character_limit: int | None = None,
        transcript_records: tuple[AgentTranscriptRecord, ...] | None = None,
    ) -> list[ProviderMessage]:
        if transcript_records is None:
            transcript_records = await self.runtime_store.list_transcript_items(
                session_id=context.session_id,
                user_id=context.user_id,
                agent_id=context.agent_id,
                limit=limit,
            )
        if character_limit is not None:
            transcript_records = transcript_tail_within_character_limit(
                transcript_records,
                character_limit=character_limit,
            )
        supports_image_input = text_model_supports_image_input(
            TextProvider(context.provider),
            context.model,
        )
        image_tool_available = any(tool.name == "generate_image" for tool in tools)
        attachments_by_transcript, recent_tool_events = await asyncio.gather(
            self._history_attachments(
                context=context,
                transcript_records=transcript_records,
                include_attachments=(supports_image_input or image_tool_available),
                supports_image_input=supports_image_input,
            ),
            self._history_tool_events(
                context=context,
                tools=tools,
            ),
        )
        messages = [
            ProviderMessage(role="system", content=context.instructions),
        ]
        tool_context = available_tools_message(
            tools,
            capability_directory=capability_directory,
        )
        if tool_context is not None:
            messages.append(tool_context)
        if recent_tool_events:
            recent_tool_context = recent_tool_executions_message(
                recent_tool_events,
                tools=tools,
            )
            if recent_tool_context is not None:
                messages.append(recent_tool_context)
        timeline_message = self._conversation_timeline_message(
            context=context,
            transcript_records=transcript_records,
        )
        if timeline_message is not None:
            messages.append(timeline_message)
        for record in transcript_records:
            if record.role not in {
                AgentTranscriptRole.USER.value,
                AgentTranscriptRole.ASSISTANT.value,
            }:
                continue
            role: Literal["user", "assistant"] = (
                "user" if record.role == AgentTranscriptRole.USER.value else "assistant"
            )
            record_attachments = attachments_by_transcript.get(record.id, ())
            if role == "user" and record_attachments and supports_image_input:
                if self.attachment_service is None:
                    message = "Chat image attachments are not configured"
                    raise AttachmentValidationError(message)
                content_parts: list[ProviderInputText | ProviderInputImage] = []
                if record.text:
                    content_parts.append(ProviderInputText(text=record.text))
                image_urls = await asyncio.gather(
                    *(self.attachment_service.image_data_url(attachment) for attachment in record_attachments),
                )
                for image_url in image_urls:
                    content_parts.append(
                        ProviderInputImage(
                            image_url=image_url,
                        ),
                    )
                messages.append(
                    ProviderMessage(
                        role=role,
                        content=tuple(content_parts),
                    ),
                )
                continue
            if role == "user" and record_attachments and image_tool_available:
                content = (
                    f"{record.text}\n\n{IMAGE_TOOL_REFERENCES_AVAILABLE}"
                    if record.text
                    else IMAGE_TOOL_REFERENCES_AVAILABLE
                )
                messages.append(ProviderMessage(role=role, content=content))
                continue
            content = record.text
            if role == "user" and not content and not supports_image_input:
                content = IMAGE_HISTORY_OMITTED
            messages.append(ProviderMessage(role=role, content=content))
        return messages

    async def _history_attachments(
        self,
        *,
        context: AgentRunContext,
        transcript_records: tuple[AgentTranscriptRecord, ...],
        include_attachments: bool,
        supports_image_input: bool,
    ) -> dict[str, tuple[MessageAttachmentRecord, ...]]:
        if self.attachment_service is None or not include_attachments:
            return {}
        attachments = await self.attachment_service.list_for_transcripts(
            user_id=context.user_id,
            chat_id=context.session_id,
            transcript_ids=tuple(record.id for record in transcript_records),
        )
        if not supports_image_input:
            return attachments
        return attachments_within_media_budget(
            transcript_records=transcript_records,
            attachments_by_transcript=attachments,
            max_images=self.attachment_service.settings.max_history_images,
            max_bytes=self.attachment_service.settings.max_history_bytes,
        )

    async def _history_tool_events(
        self,
        *,
        context: AgentRunContext,
        tools: tuple[AgentTool, ...],
    ) -> tuple[AgentToolEventRecord, ...]:
        if not tools:
            return ()
        return await self.runtime_store.list_tool_events(
            session_id=context.session_id,
            user_id=context.user_id,
            agent_id=context.agent_id,
            limit=MAX_RECENT_TOOL_EVIDENCE_EVENTS,
        )

    def _conversation_timeline_message(
        self,
        *,
        context: AgentRunContext,
        transcript_records: tuple[AgentTranscriptRecord, ...],
    ) -> ProviderMessage | None:
        replayed_records = tuple(
            record
            for record in transcript_records
            if record.role
            in {
                AgentTranscriptRole.USER.value,
                AgentTranscriptRole.ASSISTANT.value,
            }
        )
        if not replayed_records:
            return None

        entries = tuple(
            TranscriptTimelineEntry.from_datetime(
                position=index,
                role=("user" if record.role == AgentTranscriptRole.USER.value else "assistant"),
                created_at=record.created_at,
            )
            for index, record in enumerate(replayed_records, start=1)
        )
        timeline = ConversationTimelineContext.from_datetimes(
            current_datetime=self.clock.now(),
            chat_started_at=context.session_started_at,
            prior_message_datetimes=tuple(record.created_at for record in replayed_records[:-1]),
            messages=entries,
        )
        return ProviderMessage(
            role="system",
            content=conversation_timeline_message(timeline),
        )

    async def _run_setup(
        self,
        *,
        user_id: str,
        provider: TextProvider,
        preferences: UserTextPreferences,
    ) -> TextRunSetup:
        credential_ref = preferences.credential_ref
        if provider == TextProvider.OPENAI_COMPATIBLE:
            credential_ref = preferences.provider_config.openai_compatible_credential_ref
        credentials = await self._credentials(
            user_id=user_id,
            provider=provider,
            credential_ref=credential_ref,
        )
        return TextRunSetup(
            preferences=preferences,
            credentials=credentials,
        )

    async def _get_or_start_run(
        self,
        *,
        chat: AgentSessionRecord,
        text: str,
        attachment_count: int,
        model: str,
        client_turn_id: str | None,
        run_id: str | None,
    ) -> AgentRunRecord:
        if chat.agent_id is None:
            message = "Text chat is missing an agent"
            raise LookupError(message)
        if run_id is not None:
            run = await self.run_store.get_run(user_id=chat.user_id, run_id=run_id)
            if run is None:
                message = "Run was not found"
                raise LookupError(message)
            if run.agent_id != chat.agent_id:
                message = "Run agent does not match chat agent"
                raise LookupError(message)
            if run.chat_id is not None and run.chat_id != chat.id:
                message = "Run chat does not match chat"
                raise LookupError(message)
            if run.session_id is not None and run.session_id != chat.id:
                message = "Run session does not match chat"
                raise LookupError(message)
            return run
        policy = await self.effective_tool_policy_service.snapshot_for_agent(
            user_id=chat.user_id,
            agent_id=chat.agent_id,
            session_id=chat.id,
        )
        async with self.run_store.session_factory() as session:
            run = await self.run_store.enqueue_run_in_session(
                session=session,
                user_id=chat.user_id,
                agent_id=chat.agent_id,
                kind=AgentRunKind.TEXT_CHAT_TURN,
                payload_json={
                    "chat_id": chat.id,
                    "message_characters": len(text),
                    "image_count": attachment_count,
                    "provider": chat.provider,
                    "model": model,
                    **({"client_turn_id": client_turn_id} if client_turn_id is not None else {}),
                },
                created_by_user_id=chat.user_id,
                session_id=chat.id,
                chat_id=chat.id,
                queue="agent-default",
                max_attempts=1,
                effective_tool_policy_json=policy.to_json(),
            )
            started = self.run_store.start_run_in_session(
                session=session,
                record=run,
                user_id=chat.user_id,
                lease_owner="api-inline",
                lease_expires_at=(utc_now() + timedelta(seconds=INLINE_RUN_LEASE_SECONDS)),
            )
            await session.commit()
        return started

    async def _run_context(
        self,
        chat: AgentSessionRecord,
        *,
        run: AgentRunRecord,
        preferences: UserTextPreferences,
        model: str,
        has_images: bool = False,
        task_query: str | None = None,
        current_location: ClientLocation | None = None,
    ) -> AgentRunContext:
        if chat.agent_id is None:
            message = "Text chat is missing an agent"
            raise LookupError(message)
        provider = TextProvider(chat.provider)
        communication_medium = communication_medium_for_channel(chat.channel)
        current_location = fresh_client_location(
            current_location,
            now=self.clock.now(),
        )
        settings = AgentInstructionSessionSettings(
            provider=provider.value,
            model=model,
            preferred_language=preferences.provider_config.preferred_language,
            session_mode="text_chat",
            communication_medium=communication_medium,
            modalities=("text", "image") if has_images else ("text",),
            base_agent_instructions=preferences.provider_config.agent_instructions,
            task_query=task_query,
            session_started_at=chat.started_at,
            current_location=current_location,
        )
        scope = AgentScope(
            owner_user_id=chat.user_id,
            agent_id=chat.agent_id,
            session_id=chat.id,
            run_id=run.id,
            channel=chat.channel,
            source=f"text.{communication_medium.value}",
        )
        instructions = await self.instruction_provider.render_instructions(
            scope=scope,
            user_id=chat.user_id,
            settings=settings,
        )
        return AgentRunContext(
            session_id=chat.id,
            run_id=run.id,
            run_kind=AgentRunKind(run.kind),
            root_run_id=run.root_run_id,
            parent_run_id=run.parent_run_id,
            user_id=chat.user_id,
            agent_id=chat.agent_id,
            provider=provider.value,
            model=model,
            instructions=instructions,
            communication_medium=communication_medium,
            effective_tool_policy_id=effective_tool_policy_id(
                run.effective_tool_policy_json,
            ),
            effective_tool_policy=effective_tool_policy_from_json(
                run.effective_tool_policy_json,
            ),
            session_started_at=chat.started_at,
            current_location=current_location,
        )

    async def condense_sms_response(
        self,
        *,
        user_id: str,
        chat_id: str,
        text: str,
    ) -> str:
        chat = await self.runtime_store.get_session(
            session_id=chat_id,
            user_id=user_id,
            channel=channel_for_communication_medium(CommunicationMedium.SMS),
        )
        if chat is None:
            message = "SMS conversation was not found"
            raise LookupError(message)
        preferences = await self._preferences(user_id)
        provider = TextProvider(chat.provider)
        credential_ref = preferences.credential_ref
        if provider == TextProvider.OPENAI_COMPATIBLE:
            credential_ref = preferences.provider_config.openai_compatible_credential_ref
        credentials = await self._credentials(
            user_id=user_id,
            provider=provider,
            credential_ref=credential_ref,
        )
        profile = response_profile(CommunicationMedium.SMS)
        parts: list[str] = []
        async for event in self.provider_client.stream_chat(
            TextProviderRequest(
                provider=provider,
                model=chat.model,
                credentials=None if credentials is None else credentials.token,
                config=preferences.provider_config,
                messages=(
                    ProviderMessage(
                        role="system",
                        content=(
                            "Rewrite the supplied draft as a plain-text SMS. Preserve its facts, qualifications, "
                            "and safety information. Use one or two short sentences and aim for no more than two "
                            "SMS segments. Return only the revised SMS."
                        ),
                    ),
                    ProviderMessage(role="user", content=text),
                ),
                tools=(),
                max_output_tokens=profile.max_output_tokens,
                reasoning_mode=TextReasoningMode.FAST,
            ),
        ):
            if event.kind == TextProviderEventKind.MESSAGE_DELTA and event.delta is not None:
                parts.append(event.delta)
            if event.kind == TextProviderEventKind.TOOL_CALLS:
                message = "SMS condensation attempted a tool call"
                raise RuntimeError(message)
        return "".join(parts).strip()

    async def _preferences(self, user_id: str) -> UserTextPreferences:
        async with self.session_factory() as session:
            service = UserTextPreferenceService(session)
            try:
                return await service.get_preferences(user_id)
            except TextPreferencesNotFoundError:
                return default_text_preferences(user_id)

    async def _credentials(
        self,
        *,
        user_id: str,
        provider: TextProvider,
        credential_ref: str | None,
    ) -> ResolvedTextCredentials | None:
        async with self.session_factory() as session:
            service = UserTextCredentialService(
                session=session,
                secret_vault=self.secret_vault,
            )
            if provider == TextProvider.OPENAI_COMPATIBLE:
                if credential_ref is None:
                    return None
                return await service.resolve_credential(
                    user_id=user_id,
                    provider=provider,
                    credential_ref=credential_ref,
                )
            if credential_ref is not None:
                try:
                    return await service.resolve_credential(
                        user_id=user_id,
                        provider=provider,
                        credential_ref=credential_ref,
                    )
                except MissingTextCredentialError:
                    pass
            credential = await service.get_latest_active_credential_record(
                user_id=user_id,
                provider=provider,
            )
            return await service.resolve_credential(
                user_id=user_id,
                provider=provider,
                credential_ref=credential.id,
            )

    async def _resolve_agent(
        self,
        *,
        user_id: str,
        agent_id: str | None,
    ) -> AgentIdentity:
        identity_service = AgentIdentityService(session_factory=self.session_factory)
        if agent_id is None:
            return await identity_service.get_or_create_default_agent(user_id)
        return await identity_service.require_agent(
            owner_user_id=user_id,
            agent_id=agent_id,
        )

    async def _append_user_message(
        self,
        *,
        context: AgentRunContext,
        text: str,
        attachment_ids: tuple[str, ...],
        client_turn_id: str | None,
        source: AgentTranscriptSource,
        owner_authenticated: bool,
    ) -> bool:
        if client_turn_id is not None:
            if self.attachment_service is None:
                message = "Chat turn idempotency is not configured"
                raise AttachmentValidationError(message)
            claimed = await self.attachment_service.claim_turn(
                user_id=context.user_id,
                chat_id=context.session_id,
                agent_id=context.agent_id,
                text=text,
                attachment_ids=attachment_ids,
                client_turn_id=client_turn_id,
            )
            return claimed.created
        if attachment_ids:
            message = "An idempotency key is required when sending images"
            raise AttachmentValidationError(message)
        await self.runtime_store.append_transcript_item(
            AgentTranscriptItem(
                session_id=context.session_id,
                user_id=context.user_id,
                agent_id=context.agent_id,
                role=AgentTranscriptRole.USER,
                text=text,
                source=source,
                owner_authenticated=owner_authenticated,
                created_at=utc_now(),
            ),
        )
        return True

    async def _append_assistant_message(
        self,
        *,
        context: AgentRunContext,
        text: str,
        source: AgentTranscriptSource,
        attachment_artifacts: tuple[AgentToolAttachmentArtifact, ...] = (),
        interrupted: bool = False,
    ) -> AgentTranscriptRecord:
        if attachment_artifacts:
            if self.attachment_service is None:
                message = "Chat image attachments are not configured"
                raise AttachmentValidationError(message)
            return await self.attachment_service.persist_assistant_with_artifacts(
                user_id=context.user_id,
                chat_id=context.session_id,
                agent_id=context.agent_id,
                text=text,
                source=source,
                attachment_ids=tuple(artifact.id for artifact in attachment_artifacts),
                notification_run_id=context.run_id,
                interrupted=interrupted,
            )
        return await self.runtime_store.append_transcript_item(
            AgentTranscriptItem(
                session_id=context.session_id,
                user_id=context.user_id,
                agent_id=context.agent_id,
                role=AgentTranscriptRole.ASSISTANT,
                text=text,
                source=source,
                interrupted=interrupted,
                is_final=not interrupted,
                notification_run_id=context.run_id,
                created_at=utc_now(),
            ),
        )

    async def _append_runtime_event(
        self,
        *,
        context: AgentRunContext,
        kind: AgentEventKind,
        payload: JsonObject,
        persisted_payload: JsonObject | None = None,
    ) -> AgentEvent:
        event = self._runtime_event(context=context, kind=kind, payload=payload)
        durable_payload = payload if persisted_payload is None else persisted_payload
        await self.runtime_store.append_runtime_event(
            AgentStateEvent(
                session_id=context.session_id,
                user_id=context.user_id,
                agent_id=context.agent_id,
                kind=AgentRuntimeEventKind(kind.value),
                payload_json=durable_payload,
                created_at=event.created_at,
            ),
        )
        if context.run_id is not None:
            run_event_kind = run_event_kind_for_agent_event(kind)
            if run_event_kind is not None:
                await self.run_store.append_event(
                    run_id=context.run_id,
                    user_id=context.user_id,
                    kind=run_event_kind,
                    payload_json=run_event_payload(
                        kind=kind,
                        payload=durable_payload,
                    ),
                    created_at=event.created_at,
                )
        return event

    def _runtime_event(
        self,
        *,
        context: AgentRunContext,
        kind: AgentEventKind,
        payload: JsonObject,
    ) -> AgentEvent:
        return AgentEvent(
            id=new_record_id(),
            kind=kind,
            session_id=context.session_id,
            run_id=context.run_id,
            user_id=context.user_id,
            agent_id=context.agent_id,
            payload=payload,
            created_at=utc_now(),
        )

    async def _append_usage(
        self,
        context: AgentRunContext,
        usage: JsonObject,
    ) -> AgentEvent:
        await self.runtime_store.append_usage_snapshot(
            AgentUsageSnapshot(
                session_id=context.session_id,
                user_id=context.user_id,
                agent_id=context.agent_id,
                provider=context.provider,
                model=context.model,
                usage_type="text_tokens",
                input_tokens=optional_int(
                    usage.get("prompt_tokens", usage.get("input_tokens")),
                ),
                output_tokens=optional_int(
                    usage.get("completion_tokens", usage.get("output_tokens")),
                ),
                total_tokens=optional_int(usage.get("total_tokens")),
                raw_usage_json=usage,
                created_at=utc_now(),
            ),
        )
        return await self._append_runtime_event(
            context=context,
            kind=AgentEventKind.USAGE,
            payload=usage,
        )

    async def _mark_run_failed(
        self,
        *,
        context: AgentRunContext,
        exc: Exception,
        manage_run: bool,
    ) -> AgentEvent:
        failure_payload: JsonObject = {
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
        if isinstance(exc, TextProviderEmptyFinalResponseError):
            failure_payload["provider_response"] = exc.summary.model_dump(mode="json")
        if manage_run and context.run_id is not None:
            await self.run_store.fail_run(
                run_id=context.run_id,
                user_id=context.user_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
                failure_category=(
                    AgentRunFailureCategory.VALIDATION
                    if isinstance(exc, ToolExecutionSecurityError)
                    else AgentRunFailureCategory.PROVIDER
                    if isinstance(
                        exc,
                        (
                            TextProviderEmptyFinalResponseError,
                            TextProviderIncompleteResponseError,
                            TextProviderRequestError,
                            TextProviderToolProtocolError,
                            TextProviderTransportError,
                        ),
                    )
                    else AgentRunFailureCategory.UNKNOWN
                ),
            )
        return await self._append_runtime_event(
            context=context,
            kind=AgentEventKind.RUN_FAILED,
            payload=failure_payload,
        )

    async def _mark_run_cancelled(
        self,
        *,
        context: AgentRunContext,
        reason: str = "cancel_requested",
    ) -> AgentEvent:
        if context.run_id is not None:
            await self.run_store.mark_cancelled(
                run_id=context.run_id,
                user_id=context.user_id,
                reason=reason,
            )
        return await self._append_runtime_event(
            context=context,
            kind=AgentEventKind.RUN_CANCELLED,
            payload={
                "reason": reason,
            },
        )

    async def _finalize_disconnected_run(self, context: AgentRunContext) -> None:
        try:
            with CancelScope(shield=True):
                await self._mark_run_cancelled(
                    context=context,
                    reason="client_disconnected",
                )
        except Exception:
            logger.exception(
                "Failed to finalize disconnected text run",
                extra={
                    "run_id": context.run_id,
                    "user_id": context.user_id,
                    "agent_id": context.agent_id,
                    "chat_id": context.session_id,
                },
            )

    async def _raise_if_cancel_requested(self, context: AgentRunContext) -> None:
        if context.run_id is None:
            return
        await self.run_store.raise_if_cancel_requested(
            run_id=context.run_id,
            user_id=context.user_id,
        )

    def _log_run_failure(
        self,
        *,
        exc: Exception,
        user_id: str,
        agent_id: str,
        chat_id: str,
        provider: str,
        model: str,
    ) -> None:
        extra = {
            "user_id": user_id,
            "agent_id": agent_id,
            "chat_id": chat_id,
            "provider": provider,
            "model": model,
        }
        if isinstance(exc, MissingTextCredentialError):
            logger.info("Text chat run missing credentials", extra=extra)
            return
        logger.exception("Text chat run failed", extra=extra)


def event_context(chat: AgentSessionRecord) -> AgentRunContext:
    if chat.agent_id is None:
        message = "Text chat is missing an agent"
        raise LookupError(message)
    return AgentRunContext(
        session_id=chat.id,
        user_id=chat.user_id,
        agent_id=chat.agent_id,
        provider=chat.provider,
        model=chat.model,
        instructions=chat.prompt_snapshot or "Deleted chat.",
        communication_medium=communication_medium_for_channel(chat.channel),
        session_started_at=chat.started_at,
    )


def transcript_tail_within_character_limit(
    records: tuple[AgentTranscriptRecord, ...],
    *,
    character_limit: int,
) -> tuple[AgentTranscriptRecord, ...]:
    groups: list[tuple[AgentTranscriptRecord, ...]] = []
    pending_user: list[AgentTranscriptRecord] = []
    for record in records:
        if record.role == AgentTranscriptRole.USER.value:
            if pending_user:
                groups.append(tuple(pending_user))
            pending_user = [record]
            continue
        if record.role == AgentTranscriptRole.ASSISTANT.value and pending_user:
            pending_user.append(record)
            groups.append(tuple(pending_user))
            pending_user = []
            continue
        if pending_user:
            groups.append(tuple(pending_user))
            pending_user = []
        groups.append((record,))
    if pending_user:
        groups.append(tuple(pending_user))

    selected_groups: list[tuple[AgentTranscriptRecord, ...]] = []
    characters = 0
    for group in reversed(groups):
        next_characters = characters + sum(len(record.text) for record in group)
        if selected_groups and next_characters > character_limit:
            break
        selected_groups.append(group)
        characters = next_characters
    return tuple(record for group in reversed(selected_groups) for record in group)


def attachments_within_media_budget(
    *,
    transcript_records: tuple[AgentTranscriptRecord, ...],
    attachments_by_transcript: dict[
        str,
        tuple[MessageAttachmentRecord, ...],
    ],
    max_images: int,
    max_bytes: int,
) -> dict[str, tuple[MessageAttachmentRecord, ...]]:
    selected: dict[str, tuple[MessageAttachmentRecord, ...]] = {}
    image_count = 0
    byte_count = 0
    for transcript in reversed(transcript_records):
        attachments = attachments_by_transcript.get(transcript.id, ())
        if not attachments:
            continue
        next_image_count = image_count + len(attachments)
        next_byte_count = byte_count + sum(attachment.byte_size for attachment in attachments)
        if next_image_count > max_images or next_byte_count > max_bytes:
            continue
        selected[transcript.id] = attachments
        image_count = next_image_count
        byte_count = next_byte_count
    return selected


def should_auto_title(chat: AgentSessionRecord) -> bool:
    return chat.title is None or chat.title == "New chat"


def available_tools_message(
    tools: tuple[AgentTool, ...],
    *,
    capability_directory: str | None = None,
) -> ProviderMessage | None:
    if not tools:
        return None
    lines = [
        "Tools available in this run:",
        (
            "This current list is authoritative for this request and overrides historical "
            "assistant claims about tool availability."
        ),
        (
            "Use a matching tool for requested current external information; do not invent "
            "or present unverified facts, reviews, prices, availability, or local place "
            "recommendations. Tool names may include collision-safe prefixes; identify their "
            "capability from both the name and description."
        ),
        (
            "Omit optional tool arguments that are not needed. Never send placeholder values "
            "such as NA, N/A, *, empty strings, or whitespace."
        ),
    ]
    routed_tools = tuple(tool for tool in tools if tool.route_action is not None)
    if routed_tools:
        lines.append("Canonical capability routes:")
        lines.extend(f"- {tool.route_action} -> {tool.name}" for tool in routed_tools)
    if any(tool.name == TOOL_SEARCH_NAME for tool in tools):
        lines.append(
            (
                "tool_search discovers callable capabilities; it does not search the web "
                "or answer the user's factual question. When a canonical web.search route "
                "is listed and current external information is needed, call that routed "
                "tool directly. Use tool_search only to discover a callable service or tool "
                "family, not merely because a research topic names a product, model, or "
                "company. "
                "When you are unsure whether a capability exists or which tool can perform "
                "the request, you must call tool_search before answering. Every returned "
                "result is authorized and callable; follow its invoke_with field. "
                "When the user names a service, tool family, or specialized capability, "
                "or asks whether one is available, and no directly attached tool matches, "
                "you must call tool_search before answering, claiming it is unavailable, or "
                "substituting a general-purpose tool. Prefer a matching authorized specialist "
                "over a generic web search."
            ),
        )
    if capability_directory is not None:
        lines.append(capability_directory)
    for tool in tools[:MAX_DETAILED_TOOL_CONTEXT_TOOLS]:
        description = compact_tool_description(
            tool.description,
            character_limit=MAX_TOOL_CONTEXT_DESCRIPTION_CHARS,
        )
        lines.append(f"- {tool.name}: {description}")
    additional_tools = tools[MAX_DETAILED_TOOL_CONTEXT_TOOLS:]
    if additional_tools:
        lines.append(
            (
                "Additional tools with compact descriptions follow; their full descriptions "
                "and schemas are attached to this request:"
            ),
        )
        for tool in additional_tools:
            description = compact_tool_description(
                tool.description,
                character_limit=MAX_ADDITIONAL_TOOL_CONTEXT_DESCRIPTION_CHARS,
            )
            lines.append(f"- {tool.name}: {description}")
    return ProviderMessage(
        role="system",
        content="\n".join(lines),
        discard_when_tools_unavailable=True,
    )


def recent_tool_executions_message(
    events: tuple[AgentToolEventRecord, ...],
    *,
    tools: tuple[AgentTool, ...],
) -> ProviderMessage | None:
    available_names = {tool.name for tool in tools}
    recent_by_name: dict[str, AgentToolEventRecord] = {}
    for event in reversed(events):
        if event.is_error or event.name not in available_names:
            continue
        recent_by_name.setdefault(event.name, event)
        if len(recent_by_name) == MAX_VISIBLE_RECENT_TOOL_EXECUTIONS:
            break
    if not recent_by_name:
        return None

    lines = [
        "Verified recent tool execution facts from earlier turns in this chat:",
        (
            "These facts come from VoxOS execution records; historical result bodies "
            "are intentionally not replayed here."
        ),
        (
            "Each listed tool is also available in the current request. If its data is "
            "needed again, call it. Do not claim that a listed execution was fabricated "
            "or that the tool is unavailable without an actual failed call in this run."
        ),
    ]
    lines.extend(
        f"- {event.name} succeeded at {event.created_at.isoformat()}."
        for event in reversed(tuple(recent_by_name.values()))
    )
    return ProviderMessage(
        role="system",
        content="\n".join(lines),
        discard_when_tools_unavailable=True,
    )


def conversation_timeline_message(
    timeline: ConversationTimelineContext,
) -> str:
    lines = [
        "# Conversation Timeline",
        f"- Current datetime (UTC): {timeline.current_datetime_utc}",
    ]
    if timeline.chat_started_at_utc is not None:
        lines.append(f"- Chat started at (UTC): {timeline.chat_started_at_utc}")
    if timeline.last_prior_message_at_utc is not None:
        lines.append(
            f"- Last prior message at (UTC): {timeline.last_prior_message_at_utc}",
        )
    lines.append(
        "- Replayed message datetimes below correspond to the user and assistant messages in this request.",
    )
    for entry in timeline.messages:
        lines.append(
            f"- [{entry.position}] {entry.role} at {entry.created_at_utc}",
        )
    return "\n".join(lines)


def compact_tool_description(
    description: str,
    *,
    character_limit: int,
) -> str:
    compacted = " ".join(description.split())
    if len(compacted) <= character_limit:
        return compacted
    return f"{compacted[: character_limit - 3]}..."


def title_from_message(text: str) -> str:
    stripped = " ".join(text.split())
    if len(stripped) <= 64:
        return stripped
    return f"{stripped[:61]}..."


def prompt_hash(prompt: str) -> str:
    return sha256(prompt.encode("utf-8")).hexdigest()


def tool_call_fingerprint(tool_call: TextProviderToolCall) -> str:
    serialized_arguments = orjson.dumps(
        tool_call.arguments_json,
        option=orjson.OPT_SORT_KEYS,
    )
    digest = sha256()
    digest.update(tool_call.name.encode("utf-8"))
    digest.update(b"\0")
    digest.update(serialized_arguments)
    return digest.hexdigest()


def tool_activity_payload(prepared_call: PreparedToolCall) -> JsonObject:
    tool = prepared_call.activity_tool or prepared_call.tool
    activity = None if tool is None else tool.activity
    if activity is None:
        raw_name = (
            prepared_call.tool_call.name if tool is None else (tool.catalog_label or tool.source_tool_name or tool.name)
        )
        display_name = " ".join(raw_name.replace("_", " ").replace("-", " ").split())
        activity = AgentToolActivity(
            label=f"Using {display_name or 'a tool'}"[:128],
        )
    return activity.model_dump(mode="json", exclude_none=True)


def tool_result_error_message(output_text: str) -> str:
    stripped = output_text.strip()
    if not stripped:
        return "Tool returned an error"
    try:
        payload = orjson.loads(stripped)
    except orjson.JSONDecodeError:
        return stripped[:MAX_TOOL_ERROR_MESSAGE_CHARS]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, str) and error.strip():
            return error.strip()[:MAX_TOOL_ERROR_MESSAGE_CHARS]
    return stripped[:MAX_TOOL_ERROR_MESSAGE_CHARS]


def tool_failure_recovery_output(error_message: str) -> str:
    detail = error_message.strip()[:MAX_TOOL_ERROR_MESSAGE_CHARS]
    if not detail:
        detail = "The tool returned an error"
    if detail[-1] not in ".!?":
        detail = f"{detail}."
    return (
        f"{detail} The tool failed and did not produce a usable result. Continue with "
        "any successful results and give the user a final response. Do not claim that "
        "the tool succeeded; explain what could not be completed or verified."
    )


def validate_tool_call_arguments(
    tool: AgentTool | None,
    arguments_json: JsonObject,
) -> JsonObject:
    validate_llm_tool_arguments(
        arguments_json,
        allowed_paths=(frozenset() if tool is None else tool.authority_argument_allowed_paths),
    )
    if tool is None:
        return dict(arguments_json)
    accepted_arguments = prune_unsupported_tool_arguments(
        arguments_json,
        tool.parameters_schema,
    )
    validate_tool_arguments_schema(accepted_arguments, tool.parameters_schema)
    return accepted_arguments


def append_tool_call_output(
    *,
    messages: list[ProviderMessage],
    continuation_items: list[JsonObject] | None,
    provider: TextProvider = TextProvider.OPENAI,
    call_id: str,
    output: str,
) -> None:
    if continuation_items is None:
        messages.append(
            ProviderMessage(
                role="tool",
                tool_call_id=call_id,
                content=output,
            ),
        )
        return
    continuation_items.append(
        provider_function_call_output_item(
            provider,
            call_id=call_id,
            output=output,
        ),
    )


def run_event_kind_for_agent_event(kind: AgentEventKind) -> AgentRunEventKind | None:
    if kind == AgentEventKind.MESSAGE_COMPLETED:
        return AgentRunEventKind.MESSAGE_COMPLETED
    if kind == AgentEventKind.TOOL_CALL_STARTED:
        return AgentRunEventKind.TOOL_CALL_STARTED
    if kind == AgentEventKind.TOOL_CALL_COMPLETED:
        return AgentRunEventKind.TOOL_CALL_COMPLETED
    if kind == AgentEventKind.TOOL_CALL_FAILED:
        return AgentRunEventKind.TOOL_CALL_FAILED
    if kind == AgentEventKind.USAGE:
        return AgentRunEventKind.USAGE
    return None


def run_event_payload(kind: AgentEventKind, payload: JsonObject) -> JsonObject:
    if kind == AgentEventKind.MESSAGE_COMPLETED:
        text = payload.get("text")
        return {"characters_count": len(text) if isinstance(text, str) else 0}
    if kind == AgentEventKind.TOOL_CALL_STARTED:
        return {
            "call_id": payload.get("call_id"),
            "name": payload.get("name"),
        }
    if kind == AgentEventKind.TOOL_CALL_COMPLETED:
        output = payload.get("output")
        return {
            "call_id": payload.get("call_id"),
            "name": payload.get("name"),
            "is_error": payload.get("is_error"),
            "output_characters": len(output) if isinstance(output, str) else 0,
        }
    if kind == AgentEventKind.TOOL_CALL_FAILED:
        return {
            "call_id": payload.get("call_id"),
            "name": payload.get("name"),
            "error_type": payload.get("error_type"),
            "error_message": payload.get("error_message"),
        }
    if kind == AgentEventKind.USAGE:
        return payload
    return {}


def optional_int(value: object) -> int | None:
    if isinstance(value, int) and value >= 0:
        return value
    return None

import re
from dataclasses import dataclass
from hashlib import sha256

from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_core.instructions import AGENT_INSTRUCTION_PROMPT_VERSION
from voxos.agent_core.response_profiles import (
    CommunicationMedium,
    response_profile_prompt_snapshot,
)
from voxos.agent_identity.models import AgentIdentity
from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.models import (
    AgentTranscriptItem,
    AgentTranscriptRole,
    AgentTranscriptSource,
)
from voxos.agent_runtime.records import AgentRunRecord, AgentSessionRecord
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.storage import SqlModelAgentRuntimeStore
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.text.errors import TextPreferencesNotFoundError
from voxos.text.models import (
    TextProvider,
    UserTextPreferences,
    default_text_provider_config,
    effective_text_model,
    text_provider_definition,
)
from voxos.text.storage import UserTextPreferenceService

TEXT_CHAT_CHANNEL = "text"
RUN_UPDATE_STAGE_PATTERN = re.compile(r"^[a-z0-9_-]{1,20}$")


class RunChatValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RunChatDeliveryResult:
    delivered: bool
    transcript_id: str | None = None
    created: bool = False
    unavailable_reason: str | None = None


@dataclass(frozen=True, slots=True)
class RunChatService:
    session_factory: AsyncSessionFactory
    runtime_store: SqlModelAgentRuntimeStore
    run_store: SqlModelAgentRunStore

    async def require_agent(self, *, user_id: str, agent_id: str) -> AgentIdentity:
        return await AgentIdentityService(session_factory=self.session_factory).require_agent(
            owner_user_id=user_id,
            agent_id=agent_id,
        )

    async def create_chat(
        self,
        *,
        user_id: str,
        agent_id: str,
        title: str | None,
        provider: TextProvider | None = None,
        model: str | None = None,
    ) -> AgentSessionRecord:
        async with self.session_factory() as session:
            chat = await self.create_chat_in_session(
                session=session,
                user_id=user_id,
                agent_id=agent_id,
                title=title,
                provider=provider,
                model=model,
            )
            await session.commit()
            await session.refresh(chat)
            return chat

    async def resolve_or_create_chat_in_session(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        agent_id: str,
        chat_id: str | None,
        title: str | None,
        provider: TextProvider | None = None,
        model: str | None = None,
    ) -> AgentSessionRecord:
        if chat_id is not None:
            return await self.require_chat_in_session(
                session=session,
                user_id=user_id,
                agent_id=agent_id,
                chat_id=chat_id,
            )
        return await self.create_chat_in_session(
            session=session,
            user_id=user_id,
            agent_id=agent_id,
            title=title,
            provider=provider,
            model=model,
        )

    async def create_chat_in_session(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        agent_id: str,
        title: str | None,
        provider: TextProvider | None = None,
        model: str | None = None,
    ) -> AgentSessionRecord:
        preferences = await text_preferences(session=session, user_id=user_id)
        resolved_provider = provider or preferences.provider
        provider_definition = text_provider_definition(resolved_provider)
        resolved_model = model or (
            effective_text_model(preferences)
            if resolved_provider == preferences.provider
            else preferences.provider_config.available_models[0]
            if (resolved_provider == TextProvider.OPENAI_COMPATIBLE and preferences.provider_config.available_models)
            else provider_definition.default_model
        )
        prompt_snapshot = response_profile_prompt_snapshot(
            preferences.provider_config.agent_instructions,
            CommunicationMedium.WEB_CHAT,
        )
        chat = AgentSessionRecord(
            user_id=user_id,
            agent_id=agent_id,
            provider=resolved_provider.value,
            model=resolved_model,
            channel=TEXT_CHAT_CHANNEL,
            title=normalized_title(title) or "New chat",
            voice=None,
            room_name=None,
            room_metadata={},
            prompt_version=AGENT_INSTRUCTION_PROMPT_VERSION,
            prompt_snapshot=prompt_snapshot,
            prompt_hash=prompt_hash(prompt_snapshot),
        )
        session.add(chat)
        await session.flush()
        return chat

    async def require_chat_in_session(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        agent_id: str,
        chat_id: str,
    ) -> AgentSessionRecord:
        chat = await session.get(AgentSessionRecord, chat_id)
        if (
            chat is None
            or chat.user_id != user_id
            or chat.agent_id != agent_id
            or chat.channel != TEXT_CHAT_CHANNEL
            or chat.deleted_at is not None
        ):
            message = "Chat must be an active text chat owned by the run user and agent"
            raise RunChatValidationError(message)
        return chat

    async def ensure_run_chat(
        self,
        *,
        run: AgentRunRecord,
        title: str,
        provider: TextProvider | None = None,
        model: str | None = None,
    ) -> AgentSessionRecord:
        if run.chat_id is not None:
            async with self.session_factory() as session:
                return await self.require_chat_in_session(
                    session=session,
                    user_id=run.user_id,
                    agent_id=run.agent_id,
                    chat_id=run.chat_id,
                )
        chat = await self.create_chat(
            user_id=run.user_id,
            agent_id=run.agent_id,
            title=title,
            provider=provider,
            model=model,
        )
        bound = await self.run_store.bind_run_to_chat(
            run_id=run.id,
            user_id=run.user_id,
            chat_id=chat.id,
        )
        if bound is None:
            message = "Run was not found while binding its chat"
            raise LookupError(message)
        return chat

    async def deliver_to_run(
        self,
        *,
        run: AgentRunRecord,
        text: str,
        stage: str,
        source: AgentTranscriptSource = AgentTranscriptSource.RUN_UPDATE,
    ) -> RunChatDeliveryResult:
        return await self._deliver(
            run=run,
            chat_id=run.chat_id,
            expected_agent_id=run.agent_id,
            text=text,
            stage=stage,
            source=source,
        )

    async def deliver_to_parent(
        self,
        *,
        run: AgentRunRecord,
        text: str,
        stage: str = "complete",
    ) -> RunChatDeliveryResult:
        return await self._deliver(
            run=run,
            chat_id=run.parent_session_id,
            expected_agent_id=None,
            text=text,
            stage=stage,
            source=AgentTranscriptSource.SUBAGENT_OUTPUT,
        )

    async def _deliver(
        self,
        *,
        run: AgentRunRecord,
        chat_id: str | None,
        expected_agent_id: str | None,
        text: str,
        stage: str,
        source: AgentTranscriptSource,
    ) -> RunChatDeliveryResult:
        normalized_text = text.strip()
        if not normalized_text:
            return RunChatDeliveryResult(
                delivered=False,
                unavailable_reason="empty_message",
            )
        client_turn_id = run_update_client_turn_id(run_id=run.id, stage=stage)
        if chat_id is None:
            return RunChatDeliveryResult(
                delivered=False,
                unavailable_reason="chat_not_linked",
            )
        chat = await self.runtime_store.get_session(
            session_id=chat_id,
            user_id=run.user_id,
            channel=TEXT_CHAT_CHANNEL,
        )
        if (
            chat is None
            or chat.agent_id is None
            or (expected_agent_id is not None and chat.agent_id != expected_agent_id)
        ):
            return RunChatDeliveryResult(
                delivered=False,
                unavailable_reason="chat_unavailable",
            )
        result = await self.runtime_store.append_chat_message_once(
            AgentTranscriptItem(
                session_id=chat.id,
                user_id=run.user_id,
                agent_id=chat.agent_id,
                role=AgentTranscriptRole.ASSISTANT,
                text=normalized_text,
                source=source,
                client_turn_id=client_turn_id,
                notification_run_id=run.id,
                created_at=utc_now(),
            ),
        )
        if result is None:
            return RunChatDeliveryResult(
                delivered=False,
                unavailable_reason="chat_unavailable",
            )
        return RunChatDeliveryResult(
            delivered=True,
            transcript_id=result.record.id,
            created=result.created,
        )


async def text_preferences(
    *,
    session: AsyncSession,
    user_id: str,
) -> UserTextPreferences:
    service = UserTextPreferenceService(session)
    try:
        return await service.get_preferences(user_id)
    except TextPreferencesNotFoundError:
        return default_text_preferences(user_id)


def default_text_preferences(user_id: str) -> UserTextPreferences:
    definition = text_provider_definition(TextProvider.OPENAI)
    return UserTextPreferences(
        user_id=user_id,
        provider=TextProvider.OPENAI,
        model=definition.default_model,
        credential_ref=None,
        provider_config=default_text_provider_config(TextProvider.OPENAI),
    )


def normalized_title(title: str | None) -> str | None:
    if title is None:
        return None
    stripped = title.strip()
    return stripped[:256] or None


def prompt_hash(prompt_snapshot: str) -> str:
    return sha256(prompt_snapshot.encode("utf-8")).hexdigest()


def run_update_client_turn_id(*, run_id: str, stage: str) -> str:
    normalized_stage = stage.strip().lower()
    if RUN_UPDATE_STAGE_PATTERN.fullmatch(normalized_stage) is None:
        message = "Run update stage must use 1-20 lowercase letters, numbers, hyphens, or underscores"
        raise ValueError(message)
    client_turn_id = f"run:{run_id}:{normalized_stage}"
    if len(client_turn_id) > 64:
        message = "Run update client turn ID exceeds the transcript limit"
        raise ValueError(message)
    return client_turn_id

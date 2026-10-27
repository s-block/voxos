from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256

from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from voxos.agent_core.instructions import AGENT_INSTRUCTION_PROMPT_VERSION
from voxos.agent_core.response_profiles import (
    CommunicationMedium,
    channel_for_communication_medium,
    response_profile,
    response_profile_prompt_snapshot,
)
from voxos.agent_core.time_context import Clock, SystemClock, ensure_aware_utc
from voxos.agent_runtime.models import AgentSessionStatus
from voxos.agent_runtime.records import AgentSessionRecord
from voxos.integrations.models import IntegrationProviderType
from voxos.integrations.records import IntegrationConversationRecord
from voxos.text.errors import TextPreferencesNotFoundError
from voxos.text.models import (
    TextProvider,
    TextProviderConfig,
    UserTextPreferences,
    text_provider_definition,
)
from voxos.text.storage import UserTextPreferenceService

SMS_CONVERSATION_IDLE_TIMEOUT = timedelta(hours=24)
SUPPORTED_INTEGRATION_CONVERSATION_MEDIA = frozenset(
    {
        CommunicationMedium.SMS,
        CommunicationMedium.WHATSAPP,
    },
)


def validate_conversation_provider_medium(
    *,
    provider: IntegrationProviderType,
    medium: CommunicationMedium,
) -> None:
    if medium not in SUPPORTED_INTEGRATION_CONVERSATION_MEDIA:
        message = f"Unsupported integration conversation medium: {medium.value}"
        raise ValueError(message)
    if medium == CommunicationMedium.WHATSAPP:
        if provider == IntegrationProviderType.WHATSAPP:
            return
    elif provider in {
        IntegrationProviderType.TELNYX,
        IntegrationProviderType.TWILIO,
    }:
        return
    message = f"Provider {provider.value} does not support {medium.value} conversations"
    raise ValueError(message)


@dataclass(frozen=True, slots=True)
class IntegrationConversationService:
    session: AsyncSession
    clock: Clock = field(default_factory=SystemClock)
    idle_timeout: timedelta = SMS_CONVERSATION_IDLE_TIMEOUT

    async def resolve_sms_conversation(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str,
        provider: IntegrationProviderType,
        remote_peer: str,
        agent_id: str,
    ) -> IntegrationConversationRecord:
        return await self.resolve_conversation(
            user_id=user_id,
            integration_id=integration_id,
            resource_id=resource_id,
            provider=provider,
            medium=CommunicationMedium.SMS,
            remote_peer=remote_peer,
            agent_id=agent_id,
        )

    async def resolve_conversation(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str,
        provider: IntegrationProviderType,
        medium: CommunicationMedium,
        remote_peer: str,
        agent_id: str,
    ) -> IntegrationConversationRecord:
        validate_conversation_provider_medium(provider=provider, medium=medium)
        preferences = await self._text_preferences(user_id)
        now = self.clock.now()
        statement = self._conversation_statement(
            user_id=user_id,
            integration_id=integration_id,
            resource_id=resource_id,
            provider=provider,
            medium=medium,
            remote_peer=remote_peer,
            agent_id=agent_id,
        ).with_for_update()
        conversation = (await self.session.exec(statement)).one_or_none()
        if conversation is not None:
            return await self._reuse_or_rollover(
                conversation=conversation,
                preferences=preferences,
                now=now,
            )

        runtime_session = self._new_runtime_session(
            user_id=user_id,
            agent_id=agent_id,
            medium=medium,
            remote_peer=remote_peer,
            preferences=preferences,
            now=now,
        )
        conversation = IntegrationConversationRecord(
            user_id=user_id,
            integration_id=integration_id,
            resource_id=resource_id,
            provider=provider.value,
            medium=medium.value,
            remote_peer=remote_peer,
            agent_id=agent_id,
            runtime_session_id=runtime_session.id,
            last_interaction_at=now,
            expires_at=now + self.idle_timeout,
            created_at=now,
            updated_at=now,
        )
        self.session.add(runtime_session)
        self.session.add(conversation)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            concurrent = (
                await self.session.exec(
                    self._conversation_statement(
                        user_id=user_id,
                        integration_id=integration_id,
                        resource_id=resource_id,
                        provider=provider,
                        medium=medium,
                        remote_peer=remote_peer,
                        agent_id=agent_id,
                    ),
                )
            ).one_or_none()
            if concurrent is None:
                raise
            return await self._reuse_or_rollover(
                conversation=concurrent,
                preferences=preferences,
                now=now,
            )
        await self.session.refresh(conversation)
        return conversation

    async def _reuse_or_rollover(
        self,
        *,
        conversation: IntegrationConversationRecord,
        preferences: UserTextPreferences,
        now: datetime,
    ) -> IntegrationConversationRecord:
        if ensure_aware_utc(conversation.expires_at) <= now:
            previous_session = await self.session.get(
                AgentSessionRecord,
                conversation.runtime_session_id,
            )
            if previous_session is not None and previous_session.user_id == conversation.user_id:
                previous_session.status = AgentSessionStatus.ENDED.value
                previous_session.ended_at = now
                previous_session.close_reason = "idle_rollover"
                previous_session.updated_at = now
            runtime_session = self._new_runtime_session(
                user_id=conversation.user_id,
                agent_id=conversation.agent_id,
                medium=CommunicationMedium(conversation.medium),
                remote_peer=conversation.remote_peer,
                preferences=preferences,
                now=now,
            )
            self.session.add(runtime_session)
            conversation.runtime_session_id = runtime_session.id
        conversation.last_interaction_at = now
        conversation.expires_at = now + self.idle_timeout
        conversation.updated_at = now
        await self.session.commit()
        await self.session.refresh(conversation)
        return conversation

    def _new_runtime_session(
        self,
        *,
        user_id: str,
        agent_id: str,
        medium: CommunicationMedium,
        remote_peer: str,
        preferences: UserTextPreferences,
        now: datetime,
    ) -> AgentSessionRecord:
        profile = response_profile(medium)
        prompt_snapshot = response_profile_prompt_snapshot(
            preferences.provider_config.agent_instructions,
            medium,
        )
        medium_label = "WhatsApp" if medium == CommunicationMedium.WHATSAPP else medium.value.upper()
        return AgentSessionRecord(
            user_id=user_id,
            agent_id=agent_id,
            provider=preferences.provider.value,
            model=preferences.model,
            channel=channel_for_communication_medium(medium),
            title=(f"SMS from {remote_peer}" if medium == CommunicationMedium.SMS else f"{medium_label} conversation"),
            voice=None,
            room_name=None,
            room_metadata={"response_profile": profile.identifier},
            effective_tool_policy_json={},
            prompt_version=AGENT_INSTRUCTION_PROMPT_VERSION,
            prompt_snapshot=prompt_snapshot,
            prompt_hash=sha256(prompt_snapshot.encode()).hexdigest(),
            started_at=now,
            created_at=now,
            updated_at=now,
        )

    def _conversation_statement(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str,
        provider: IntegrationProviderType,
        medium: CommunicationMedium,
        remote_peer: str,
        agent_id: str,
    ) -> SelectOfScalar[IntegrationConversationRecord]:
        return select(IntegrationConversationRecord).where(
            col(IntegrationConversationRecord.user_id) == user_id,
            col(IntegrationConversationRecord.integration_id) == integration_id,
            col(IntegrationConversationRecord.resource_id) == resource_id,
            col(IntegrationConversationRecord.provider) == provider.value,
            col(IntegrationConversationRecord.medium) == medium.value,
            col(IntegrationConversationRecord.remote_peer) == remote_peer,
            col(IntegrationConversationRecord.agent_id) == agent_id,
        )

    async def _text_preferences(self, user_id: str) -> UserTextPreferences:
        try:
            return await UserTextPreferenceService(self.session).get_preferences(user_id)
        except TextPreferencesNotFoundError:
            provider = TextProvider.OPENAI
            return UserTextPreferences(
                user_id=user_id,
                provider=provider,
                model=text_provider_definition(provider).default_model,
                provider_config=TextProviderConfig(),
            )

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_core.response_profiles import CommunicationMedium, response_profile
from voxos.agent_runtime.models import AgentTranscriptRole, AgentTranscriptSource
from voxos.agent_runtime.records import AgentTranscriptRecord
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.integrations.models import (
    CommunicationOutboundSmsRequest,
    IntegrationActionResponse,
    IntegrationDirection,
    IntegrationMessageStatus,
    IntegrationProviderType,
    IntegrationReplyStatus,
    IntegrationResourceStatus,
    IntegrationStatus,
)
from voxos.integrations.provider_registry import IntegrationProviderRegistry
from voxos.integrations.providers.telnyx.provider import TelnyxIntegrationProvider
from voxos.integrations.providers.twilio.provider import TwilioIntegrationProvider
from voxos.integrations.records import (
    IntegrationConversationRecord,
    IntegrationMessageRecord,
    IntegrationReplyRecord,
    IntegrationResourceRecord,
    UserIntegrationRecord,
)
from voxos.integrations.services import UserIntegrationService
from voxos.integrations.sms_segments import SmsEncoding, analyze_sms_segments
from voxos.integrations.whatsapp_jids import (
    whatsapp_conversation_peer_from_payload,
    whatsapp_target_jid_from_payload,
)
from voxos.whatsapp.models import (
    WHATSAPP_MAX_TEXT_BYTES,
    WhatsAppDesiredState,
    WhatsAppOutboxStatus,
)
from voxos.whatsapp.records import WhatsAppOutboxRecord, WhatsAppSessionRecord

SMS_DEFAULT_SEGMENT_LIMIT = 2
SMS_EXTENDED_SEGMENT_LIMIT = 3
SMS_OVERFLOW_FALLBACK = (
    "I have a longer answer, but it will not fit well in SMS. Ask for one part at a time and I'll keep it brief."
)
DISPATCH_INTERRUPTED_ERROR = (
    "SMS dispatch was interrupted after provider submission began; automatic resend is blocked."
)
AMBIGUOUS_DELIVERY_ERROR = "SMS delivery is ambiguous; automatic resend is blocked."
TERMINAL_FAILED_ERROR = "SMS reply is in a terminal failed state."
SUPPRESSED_REPLY_ERROR = "SMS reply was suppressed by application policy."
TRANSCRIPT_BODY_MISMATCH_ERROR = "Delivered SMS body does not match the durable reply."
PROVIDER_RESULT_AMBIGUOUS_ERROR = "SMS provider result is ambiguous; automatic resend is blocked."
OUTBOUND_RECORD_MISSING_ERROR = "SMS provider accepted the reply but the outbound record was not found."
REPLY_CONTEXT_MISSING_ERROR = "Durable SMS reply context was not found."
REPLY_CONTEXT_INVALID_ERROR = "Durable SMS reply context is invalid."
EMPTY_REPLY_ERROR = "The agent generated an empty SMS reply."
INVALID_FALLBACK_ERROR = "The SMS overflow fallback is invalid."
INCOMPLETE_REPLY_ERROR = "Accepted SMS reply metadata is incomplete."
EMPTY_WHATSAPP_REPLY_ERROR = "The agent generated an empty WhatsApp reply."
WHATSAPP_REPLY_TOO_LARGE_ERROR = "The WhatsApp reply exceeds the supported text limit."
WHATSAPP_TARGET_INVALID_ERROR = "The durable WhatsApp reply target is invalid."
WHATSAPP_OUTBOX_CONTEXT_INVALID_ERROR = "The durable WhatsApp outbox context is invalid."
WHATSAPP_REPLY_STATE_INVALID_ERROR = "The durable WhatsApp reply state is invalid."
WHATSAPP_INTEGRATION_UNAVAILABLE_ERROR = "The WhatsApp integration is no longer connected."


class CommunicationReplyDispatchError(RuntimeError):
    pass


class AmbiguousCommunicationReplyError(CommunicationReplyDispatchError):
    pass


class CommunicationReplyDispatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    communication_medium: CommunicationMedium = CommunicationMedium.SMS
    reply_id: str = Field(min_length=1, max_length=36)
    outbound_message_id: str | None = Field(default=None, min_length=1, max_length=36)
    whatsapp_outbox_id: str | None = Field(default=None, min_length=1, max_length=36)
    whatsapp_outbox_status: WhatsAppOutboxStatus | None = None
    status: IntegrationReplyStatus
    body: str = Field(min_length=1)
    encoding: SmsEncoding | None = None
    estimated_segments: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_medium_metadata(self) -> "CommunicationReplyDispatchResult":
        if self.communication_medium == CommunicationMedium.SMS:
            if (
                self.outbound_message_id is None
                or self.encoding is None
                or self.estimated_segments is None
                or self.whatsapp_outbox_id is not None
                or self.whatsapp_outbox_status is not None
            ):
                raise ValueError(INCOMPLETE_REPLY_ERROR)
            return self
        if self.communication_medium == CommunicationMedium.WHATSAPP:
            if (
                self.whatsapp_outbox_id is None
                or self.whatsapp_outbox_status is None
                or self.outbound_message_id is not None
                or self.encoding is not None
                or self.estimated_segments is not None
            ):
                raise ValueError(WHATSAPP_OUTBOX_CONTEXT_INVALID_ERROR)
            return self
        message = f"Unsupported communication reply medium: {self.communication_medium.value}"
        raise ValueError(message)


@dataclass(frozen=True, slots=True)
class ReplyRecords:
    reply: IntegrationReplyRecord
    conversation: IntegrationConversationRecord
    inbound_message: IntegrationMessageRecord


@dataclass(frozen=True, slots=True)
class LockedWhatsAppReplyRecords:
    records: ReplyRecords
    whatsapp_session: WhatsAppSessionRecord | None
    outbox: WhatsAppOutboxRecord | None


@dataclass(frozen=True, slots=True)
class CommunicationReplyDispatcher:
    session_factory: AsyncSessionFactory
    secret_vault: UserSecretVaultService
    public_base_url: str | None
    provider_registry: IntegrationProviderRegistry = field(
        default_factory=lambda: IntegrationProviderRegistry(
            telnyx=TelnyxIntegrationProvider(),
            twilio=TwilioIntegrationProvider(),
        ),
    )

    async def accepted_result(
        self,
        *,
        user_id: str,
        run_id: str,
        inbound_message_id: str,
        conversation_id: str,
        communication_medium: CommunicationMedium = CommunicationMedium.SMS,
    ) -> CommunicationReplyDispatchResult | None:
        async with self.session_factory() as session:
            records = await self._records(
                session=session,
                user_id=user_id,
                run_id=run_id,
                inbound_message_id=inbound_message_id,
                conversation_id=conversation_id,
                for_update=(communication_medium == CommunicationMedium.SMS),
            )
            require_reply_medium(records, communication_medium)
            if communication_medium == CommunicationMedium.WHATSAPP:
                locked = await self._lock_whatsapp_reply_records(
                    session=session,
                    initial_records=records,
                    user_id=user_id,
                    run_id=run_id,
                    inbound_message_id=inbound_message_id,
                    conversation_id=conversation_id,
                )
                records = locked.records
                outbox = locked.outbox
                status = IntegrationReplyStatus(records.reply.status)
                if outbox is not None:
                    outbox_status = WhatsAppOutboxStatus(outbox.status)
                    if (
                        status == IntegrationReplyStatus.GENERATED
                        and outbox_status
                        in {
                            WhatsAppOutboxStatus.QUEUED,
                            WhatsAppOutboxStatus.SUBMITTING,
                        }
                    ) or (
                        status
                        in {
                            IntegrationReplyStatus.PROVIDER_ACCEPTED,
                            IntegrationReplyStatus.DELIVERED,
                        }
                        and outbox_status == WhatsAppOutboxStatus.SENT
                    ):
                        return whatsapp_dispatch_result(
                            reply=records.reply,
                            outbox=outbox,
                        )
                    if status == IntegrationReplyStatus.AMBIGUOUS or outbox_status == WhatsAppOutboxStatus.AMBIGUOUS:
                        raise AmbiguousCommunicationReplyError(
                            AMBIGUOUS_DELIVERY_ERROR,
                        )
                    if status == IntegrationReplyStatus.FAILED or outbox_status == WhatsAppOutboxStatus.FAILED:
                        raise CommunicationReplyDispatchError(
                            TERMINAL_FAILED_ERROR,
                        )
                    raise CommunicationReplyDispatchError(
                        WHATSAPP_REPLY_STATE_INVALID_ERROR,
                    )
                if status == IntegrationReplyStatus.GENERATED:
                    raise CommunicationReplyDispatchError(
                        WHATSAPP_OUTBOX_CONTEXT_INVALID_ERROR,
                    )
            else:
                status = IntegrationReplyStatus(records.reply.status)
            if status in {
                IntegrationReplyStatus.PROVIDER_ACCEPTED,
                IntegrationReplyStatus.DELIVERED,
            }:
                if communication_medium != CommunicationMedium.SMS:
                    raise CommunicationReplyDispatchError(
                        WHATSAPP_REPLY_STATE_INVALID_ERROR,
                    )
                return dispatch_result(records.reply)
            if status == IntegrationReplyStatus.DISPATCHING:
                records.reply.status = IntegrationReplyStatus.AMBIGUOUS.value
                records.reply.error_code = "dispatch_interrupted"
                records.reply.updated_at = utc_now()
                await session.commit()
                raise AmbiguousCommunicationReplyError(DISPATCH_INTERRUPTED_ERROR)
            if status == IntegrationReplyStatus.AMBIGUOUS:
                raise AmbiguousCommunicationReplyError(AMBIGUOUS_DELIVERY_ERROR)
            if status == IntegrationReplyStatus.FAILED:
                raise CommunicationReplyDispatchError(TERMINAL_FAILED_ERROR)
            if status == IntegrationReplyStatus.SUPPRESSED:
                raise CommunicationReplyDispatchError(SUPPRESSED_REPLY_ERROR)
            return None

    async def ensure_inbound_transcript(
        self,
        *,
        user_id: str,
        run_id: str,
        inbound_message_id: str,
        conversation_id: str,
    ) -> None:
        async with self.session_factory() as session:
            records = await self._records(
                session=session,
                user_id=user_id,
                run_id=run_id,
                inbound_message_id=inbound_message_id,
                conversation_id=conversation_id,
                for_update=True,
            )
            if records.reply.inbound_transcript_recorded_at is not None:
                return
            created_at = records.inbound_message.received_at or records.inbound_message.created_at
            session.add(
                AgentTranscriptRecord(
                    session_id=records.conversation.runtime_session_id,
                    user_id=user_id,
                    agent_id=records.conversation.agent_id,
                    role=AgentTranscriptRole.USER.value,
                    text=inbound_transcript_text(records.inbound_message),
                    source=AgentTranscriptSource.TEXT_INPUT.value,
                    created_at=created_at,
                ),
            )
            records.reply.inbound_transcript_recorded_at = utc_now()
            records.reply.updated_at = utc_now()
            await session.commit()

    async def ensure_outbound_transcript(
        self,
        *,
        user_id: str,
        run_id: str,
        inbound_message_id: str,
        conversation_id: str,
        body: str,
    ) -> None:
        async with self.session_factory() as session:
            records = await self._records(
                session=session,
                user_id=user_id,
                run_id=run_id,
                inbound_message_id=inbound_message_id,
                conversation_id=conversation_id,
                for_update=True,
            )
            if records.reply.outbound_transcript_recorded_at is not None:
                return
            if records.reply.generated_body != body:
                raise CommunicationReplyDispatchError(TRANSCRIPT_BODY_MISMATCH_ERROR)
            session.add(
                AgentTranscriptRecord(
                    session_id=records.conversation.runtime_session_id,
                    user_id=user_id,
                    agent_id=records.conversation.agent_id,
                    role=AgentTranscriptRole.ASSISTANT.value,
                    text=body,
                    source=AgentTranscriptSource.TEXT_OUTPUT.value,
                    created_at=utc_now(),
                ),
            )
            records.reply.outbound_transcript_recorded_at = utc_now()
            records.reply.updated_at = utc_now()
            await session.commit()

    async def dispatch_sms(
        self,
        *,
        user_id: str,
        run_id: str,
        inbound_message_id: str,
        conversation_id: str,
        generated_body: str,
        repairer: Callable[[str], Awaitable[str]],
    ) -> CommunicationReplyDispatchResult:
        initial_analysis = analyze_sms_segments(generated_body.strip())
        condensation_attempted = not (
            initial_analysis.within_provider_limit and initial_analysis.segments <= SMS_DEFAULT_SEGMENT_LIMIT
        )
        prepared_body = await prepare_sms_body(generated_body, repairer=repairer)
        analysis = analyze_sms_segments(prepared_body)
        async with self.session_factory() as session:
            records = await self._records(
                session=session,
                user_id=user_id,
                run_id=run_id,
                inbound_message_id=inbound_message_id,
                conversation_id=conversation_id,
                for_update=True,
            )
            require_reply_medium(records, CommunicationMedium.SMS)
            status = IntegrationReplyStatus(records.reply.status)
            if status in {
                IntegrationReplyStatus.PROVIDER_ACCEPTED,
                IntegrationReplyStatus.DELIVERED,
            }:
                return dispatch_result(records.reply)
            if status in {
                IntegrationReplyStatus.DISPATCHING,
                IntegrationReplyStatus.AMBIGUOUS,
            }:
                raise AmbiguousCommunicationReplyError(AMBIGUOUS_DELIVERY_ERROR)
            records.reply.generated_body = prepared_body
            records.reply.encoding = analysis.encoding.value
            records.reply.estimated_segments = analysis.segments
            records.reply.condensation_attempted = condensation_attempted
            records.reply.overflow_fallback_used = prepared_body == SMS_OVERFLOW_FALLBACK
            records.reply.status = IntegrationReplyStatus.GENERATED.value
            records.reply.error_code = None
            records.reply.updated_at = utc_now()
            await session.commit()

        async with self.session_factory() as session:
            records = await self._records(
                session=session,
                user_id=user_id,
                run_id=run_id,
                inbound_message_id=inbound_message_id,
                conversation_id=conversation_id,
                for_update=True,
            )
            require_reply_medium(records, CommunicationMedium.SMS)
            records.reply.status = IntegrationReplyStatus.DISPATCHING.value
            records.reply.updated_at = utc_now()
            await session.commit()

        try:
            action = await self._send_sms(records=records, body=prepared_body)
        except Exception as exc:
            await self._mark_ambiguous(
                user_id=user_id,
                inbound_message_id=inbound_message_id,
                error_code=type(exc).__name__,
            )
            raise AmbiguousCommunicationReplyError(PROVIDER_RESULT_AMBIGUOUS_ERROR) from exc

        external_id = action.external_id or action.id
        async with self.session_factory() as session:
            records = await self._records(
                session=session,
                user_id=user_id,
                run_id=run_id,
                inbound_message_id=inbound_message_id,
                conversation_id=conversation_id,
                for_update=True,
            )
            require_reply_medium(records, CommunicationMedium.SMS)
            outbound_message = (
                await session.exec(
                    select(IntegrationMessageRecord).where(
                        col(IntegrationMessageRecord.user_id) == user_id,
                        col(IntegrationMessageRecord.provider) == records.inbound_message.provider,
                        col(IntegrationMessageRecord.external_message_id) == external_id,
                        col(IntegrationMessageRecord.direction) == IntegrationDirection.OUTBOUND.value,
                    ),
                )
            ).one_or_none()
            if outbound_message is None:
                records.reply.status = IntegrationReplyStatus.AMBIGUOUS.value
                records.reply.error_code = "outbound_message_not_persisted"
                records.reply.updated_at = utc_now()
                await session.commit()
                raise AmbiguousCommunicationReplyError(OUTBOUND_RECORD_MISSING_ERROR)
            records.reply.outbound_message_id = outbound_message.id
            outbound_status = IntegrationMessageStatus(outbound_message.status)
            if outbound_status == IntegrationMessageStatus.DELIVERED:
                records.reply.status = IntegrationReplyStatus.DELIVERED.value
            elif outbound_status == IntegrationMessageStatus.FAILED:
                records.reply.status = IntegrationReplyStatus.FAILED.value
                records.reply.error_code = "provider_delivery_failed"
            else:
                records.reply.status = IntegrationReplyStatus.PROVIDER_ACCEPTED.value
            records.reply.updated_at = utc_now()
            outbound_message.segments = analysis.segments
            outbound_message.payload_json = {
                **outbound_message.payload_json,
                "response_profile": records.reply.response_profile,
                "estimated_encoding": analysis.encoding.value,
                "estimated_segments": analysis.segments,
            }
            outbound_message.updated_at = utc_now()
            await session.commit()
            await session.refresh(records.reply)
            return dispatch_result(records.reply)

    async def dispatch_whatsapp(
        self,
        *,
        user_id: str,
        run_id: str,
        inbound_message_id: str,
        conversation_id: str,
        generated_body: str,
    ) -> CommunicationReplyDispatchResult:
        prepared_body = generated_body.strip()
        if not prepared_body:
            raise CommunicationReplyDispatchError(EMPTY_WHATSAPP_REPLY_ERROR)
        if len(prepared_body.encode("utf-8")) > WHATSAPP_MAX_TEXT_BYTES:
            raise CommunicationReplyDispatchError(WHATSAPP_REPLY_TOO_LARGE_ERROR)

        async with self.session_factory() as session:
            initial_records = await self._records(
                session=session,
                user_id=user_id,
                run_id=run_id,
                inbound_message_id=inbound_message_id,
                conversation_id=conversation_id,
                for_update=False,
            )
            require_reply_medium(
                initial_records,
                CommunicationMedium.WHATSAPP,
            )
            locked = await self._lock_whatsapp_reply_records(
                session=session,
                initial_records=initial_records,
                user_id=user_id,
                run_id=run_id,
                inbound_message_id=inbound_message_id,
                conversation_id=conversation_id,
            )
            records = locked.records
            target_jid = whatsapp_target_jid(records)
            existing = locked.outbox
            if existing is not None:
                if records.reply.generated_body != prepared_body:
                    raise CommunicationReplyDispatchError(
                        WHATSAPP_OUTBOX_CONTEXT_INVALID_ERROR,
                    )
                return whatsapp_dispatch_result(
                    reply=records.reply,
                    outbox=existing,
                )

            await self._require_whatsapp_dispatch_ready(
                session=session,
                records=records,
                whatsapp_session=locked.whatsapp_session,
            )
            status = IntegrationReplyStatus(records.reply.status)
            if status not in {
                IntegrationReplyStatus.PENDING,
                IntegrationReplyStatus.GENERATED,
            }:
                raise CommunicationReplyDispatchError(
                    WHATSAPP_REPLY_STATE_INVALID_ERROR,
                )
            records.reply.generated_body = prepared_body
            records.reply.encoding = None
            records.reply.estimated_segments = None
            records.reply.condensation_attempted = False
            records.reply.overflow_fallback_used = False
            records.reply.status = IntegrationReplyStatus.GENERATED.value
            records.reply.error_code = None
            records.reply.updated_at = utc_now()
            outbox = WhatsAppOutboxRecord(
                user_id=user_id,
                integration_id=records.inbound_message.integration_id,
                resource_id=records.inbound_message.resource_id,
                reply_id=records.reply.id,
                target_jid=target_jid,
                body=prepared_body,
            )
            session.add(outbox)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                initial_records = await self._records(
                    session=session,
                    user_id=user_id,
                    run_id=run_id,
                    inbound_message_id=inbound_message_id,
                    conversation_id=conversation_id,
                    for_update=False,
                )
                locked = await self._lock_whatsapp_reply_records(
                    session=session,
                    initial_records=initial_records,
                    user_id=user_id,
                    run_id=run_id,
                    inbound_message_id=inbound_message_id,
                    conversation_id=conversation_id,
                )
                records = locked.records
                concurrent = locked.outbox
                if concurrent is None:
                    raise
                if records.reply.generated_body != prepared_body:
                    raise CommunicationReplyDispatchError(
                        WHATSAPP_OUTBOX_CONTEXT_INVALID_ERROR,
                    )
                return whatsapp_dispatch_result(
                    reply=records.reply,
                    outbox=concurrent,
                )
            return whatsapp_dispatch_result(
                reply=records.reply,
                outbox=outbox,
            )

    async def _require_whatsapp_dispatch_ready(
        self,
        *,
        session: AsyncSession,
        records: ReplyRecords,
        whatsapp_session: WhatsAppSessionRecord | None,
    ) -> None:
        integration_id = records.inbound_message.integration_id
        resource_id = records.inbound_message.resource_id
        user_id = records.reply.user_id
        integration = (
            await session.exec(
                select(UserIntegrationRecord)
                .where(
                    col(UserIntegrationRecord.id) == integration_id,
                    col(UserIntegrationRecord.user_id) == user_id,
                )
                .with_for_update(),
            )
        ).one_or_none()
        resource = (
            await session.exec(
                select(IntegrationResourceRecord)
                .where(
                    col(IntegrationResourceRecord.id) == resource_id,
                    col(IntegrationResourceRecord.user_id) == user_id,
                    col(IntegrationResourceRecord.integration_id) == integration_id,
                )
                .with_for_update(),
            )
        ).one_or_none()
        if (
            whatsapp_session is None
            or whatsapp_session.desired_state != WhatsAppDesiredState.CONNECTED.value
            or whatsapp_session.validated_at is None
            or integration is None
            or integration.provider != IntegrationProviderType.WHATSAPP.value
            or not integration.enabled
            or integration.status != IntegrationStatus.CONNECTED.value
            or integration.deleted_at is not None
            or resource is None
            or resource.provider != IntegrationProviderType.WHATSAPP.value
            or resource.status != IntegrationResourceStatus.CONNECTED.value
            or resource.deleted_at is not None
        ):
            raise CommunicationReplyDispatchError(
                WHATSAPP_INTEGRATION_UNAVAILABLE_ERROR,
            )

    async def _lock_whatsapp_reply_records(
        self,
        *,
        session: AsyncSession,
        initial_records: ReplyRecords,
        user_id: str,
        run_id: str,
        inbound_message_id: str,
        conversation_id: str,
    ) -> LockedWhatsAppReplyRecords:
        whatsapp_session = (
            await session.exec(
                select(WhatsAppSessionRecord)
                .where(
                    col(WhatsAppSessionRecord.user_id) == user_id,
                    col(WhatsAppSessionRecord.integration_id) == initial_records.inbound_message.integration_id,
                    col(WhatsAppSessionRecord.resource_id) == initial_records.inbound_message.resource_id,
                )
                .with_for_update(),
            )
        ).one_or_none()
        outbox = (
            await session.exec(
                select(WhatsAppOutboxRecord)
                .where(
                    col(WhatsAppOutboxRecord.user_id) == user_id,
                    col(WhatsAppOutboxRecord.reply_id) == initial_records.reply.id,
                )
                .with_for_update(),
            )
        ).one_or_none()
        records = await self._records(
            session=session,
            user_id=user_id,
            run_id=run_id,
            inbound_message_id=inbound_message_id,
            conversation_id=conversation_id,
            for_update=True,
        )
        require_reply_medium(records, CommunicationMedium.WHATSAPP)
        if records.reply.id != initial_records.reply.id:
            raise CommunicationReplyDispatchError(
                WHATSAPP_OUTBOX_CONTEXT_INVALID_ERROR,
            )
        if outbox is not None:
            self._validate_whatsapp_outbox(
                records=records,
                outbox=outbox,
            )
        return LockedWhatsAppReplyRecords(
            records=records,
            whatsapp_session=whatsapp_session,
            outbox=outbox,
        )

    def _validate_whatsapp_outbox(
        self,
        *,
        records: ReplyRecords,
        outbox: WhatsAppOutboxRecord,
    ) -> None:
        target_jid = whatsapp_target_jid(records)
        if (
            outbox.integration_id != records.inbound_message.integration_id
            or outbox.resource_id != records.inbound_message.resource_id
            or outbox.target_jid != target_jid
            or outbox.body != records.reply.generated_body
        ):
            raise CommunicationReplyDispatchError(
                WHATSAPP_OUTBOX_CONTEXT_INVALID_ERROR,
            )

    async def _send_sms(
        self,
        *,
        records: ReplyRecords,
        body: str,
    ) -> IntegrationActionResponse:
        async with self.session_factory() as session:
            return await UserIntegrationService(
                session=session,
                secret_vault=self.secret_vault,
                public_base_url=self.public_base_url,
                provider_registry=self.provider_registry,
                session_factory=self.session_factory,
                authorization_service=None,
            ).send_sms(
                user_id=records.reply.user_id,
                provider=IntegrationProviderType(records.inbound_message.provider),
                request=CommunicationOutboundSmsRequest(
                    integration_id=records.inbound_message.integration_id,
                    resource_id=records.inbound_message.resource_id,
                    agent_id=records.conversation.agent_id,
                    to_number=records.inbound_message.from_number,
                    body=body,
                ),
            )

    async def _mark_ambiguous(
        self,
        *,
        user_id: str,
        inbound_message_id: str,
        error_code: str,
    ) -> None:
        async with self.session_factory() as session:
            reply = (
                await session.exec(
                    select(IntegrationReplyRecord).where(
                        col(IntegrationReplyRecord.user_id) == user_id,
                        col(IntegrationReplyRecord.inbound_message_id) == inbound_message_id,
                    ),
                )
            ).one_or_none()
            if reply is None:
                return
            reply.status = IntegrationReplyStatus.AMBIGUOUS.value
            reply.error_code = error_code[:128]
            reply.updated_at = utc_now()
            await session.commit()

    async def _records(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        run_id: str,
        inbound_message_id: str,
        conversation_id: str,
        for_update: bool,
    ) -> ReplyRecords:
        statement = select(IntegrationReplyRecord).where(
            col(IntegrationReplyRecord.user_id) == user_id,
            col(IntegrationReplyRecord.inbound_message_id) == inbound_message_id,
            col(IntegrationReplyRecord.conversation_id) == conversation_id,
        )
        if for_update:
            statement = statement.with_for_update()
        reply = (await session.exec(statement)).one_or_none()
        if reply is None or reply.agent_run_id != run_id:
            raise CommunicationReplyDispatchError(REPLY_CONTEXT_MISSING_ERROR)
        conversation = await session.get(IntegrationConversationRecord, conversation_id)
        inbound_message = await session.get(IntegrationMessageRecord, inbound_message_id)
        expected_conversation_peer = (
            whatsapp_conversation_peer_from_payload(inbound_message.payload_json)
            if inbound_message is not None and inbound_message.provider == IntegrationProviderType.WHATSAPP.value
            else None
        )
        if (
            conversation is None
            or conversation.user_id != user_id
            or inbound_message is None
            or inbound_message.user_id != user_id
            or inbound_message.direction != IntegrationDirection.INBOUND.value
            or conversation.integration_id != inbound_message.integration_id
            or conversation.resource_id != inbound_message.resource_id
            or conversation.provider != inbound_message.provider
            or conversation.remote_peer != (expected_conversation_peer or inbound_message.from_number)
        ):
            raise CommunicationReplyDispatchError(REPLY_CONTEXT_INVALID_ERROR)
        records = ReplyRecords(
            reply=reply,
            conversation=conversation,
            inbound_message=inbound_message,
        )
        validate_reply_records(records)
        return records


async def prepare_sms_body(
    generated_body: str,
    *,
    repairer: Callable[[str], Awaitable[str]],
) -> str:
    stripped = generated_body.strip()
    if not stripped:
        raise CommunicationReplyDispatchError(EMPTY_REPLY_ERROR)
    analysis = analyze_sms_segments(stripped)
    if analysis.within_provider_limit and analysis.segments <= SMS_DEFAULT_SEGMENT_LIMIT:
        return stripped
    repaired = (await repairer(stripped)).strip()
    repaired_analysis = analyze_sms_segments(repaired)
    if (
        repaired
        and repaired_analysis.within_provider_limit
        and repaired_analysis.segments <= SMS_EXTENDED_SEGMENT_LIMIT
    ):
        return repaired
    fallback_analysis = analyze_sms_segments(SMS_OVERFLOW_FALLBACK)
    if not fallback_analysis.within_provider_limit:
        raise CommunicationReplyDispatchError(INVALID_FALLBACK_ERROR)
    return SMS_OVERFLOW_FALLBACK


def dispatch_result(reply: IntegrationReplyRecord) -> CommunicationReplyDispatchResult:
    if (
        reply.outbound_message_id is None
        or reply.generated_body is None
        or reply.encoding is None
        or reply.estimated_segments is None
    ):
        raise CommunicationReplyDispatchError(INCOMPLETE_REPLY_ERROR)
    return CommunicationReplyDispatchResult(
        communication_medium=CommunicationMedium.SMS,
        reply_id=reply.id,
        outbound_message_id=reply.outbound_message_id,
        status=IntegrationReplyStatus(reply.status),
        body=reply.generated_body,
        encoding=SmsEncoding(reply.encoding),
        estimated_segments=reply.estimated_segments,
    )


def whatsapp_dispatch_result(
    *,
    reply: IntegrationReplyRecord,
    outbox: WhatsAppOutboxRecord,
) -> CommunicationReplyDispatchResult:
    if reply.generated_body is None:
        raise CommunicationReplyDispatchError(
            WHATSAPP_OUTBOX_CONTEXT_INVALID_ERROR,
        )
    return CommunicationReplyDispatchResult(
        communication_medium=CommunicationMedium.WHATSAPP,
        reply_id=reply.id,
        whatsapp_outbox_id=outbox.id,
        whatsapp_outbox_status=WhatsAppOutboxStatus(outbox.status),
        status=IntegrationReplyStatus(reply.status),
        body=reply.generated_body,
    )


def validate_reply_records(records: ReplyRecords) -> None:
    try:
        medium = CommunicationMedium(records.conversation.medium)
        provider = IntegrationProviderType(records.inbound_message.provider)
    except ValueError as exc:
        raise CommunicationReplyDispatchError(REPLY_CONTEXT_INVALID_ERROR) from exc
    if medium == CommunicationMedium.SMS:
        provider_is_valid = provider in {
            IntegrationProviderType.TELNYX,
            IntegrationProviderType.TWILIO,
        }
    elif medium == CommunicationMedium.WHATSAPP:
        provider_is_valid = provider == IntegrationProviderType.WHATSAPP
    else:
        provider_is_valid = False
    if not provider_is_valid or records.reply.response_profile != response_profile(medium).identifier:
        raise CommunicationReplyDispatchError(REPLY_CONTEXT_INVALID_ERROR)


def require_reply_medium(
    records: ReplyRecords,
    expected_medium: CommunicationMedium,
) -> None:
    if records.conversation.medium != expected_medium.value:
        raise CommunicationReplyDispatchError(REPLY_CONTEXT_INVALID_ERROR)


def whatsapp_target_jid(records: ReplyRecords) -> str:
    target_jid = whatsapp_target_jid_from_payload(
        records.inbound_message.payload_json,
    )
    if target_jid is None:
        raise CommunicationReplyDispatchError(WHATSAPP_TARGET_INVALID_ERROR)
    return target_jid


def inbound_transcript_text(message: IntegrationMessageRecord) -> str:
    if message.payload_json.get("chat_kind") != "group":
        return message.body
    raw_label = message.contact_display_name or message.payload_json.get("push_name")
    label = " ".join(raw_label.split()) if isinstance(raw_label, str) and raw_label.strip() else "WhatsApp participant"
    return f"{label}: {message.body}"


async def update_reply_delivery_status(
    *,
    session: AsyncSession,
    user_id: str,
    outbound_message_id: str,
    message_status: IntegrationMessageStatus,
) -> None:
    reply = (
        await session.exec(
            select(IntegrationReplyRecord).where(
                col(IntegrationReplyRecord.user_id) == user_id,
                col(IntegrationReplyRecord.outbound_message_id) == outbound_message_id,
            ),
        )
    ).one_or_none()
    if reply is None:
        return
    current_status = IntegrationReplyStatus(reply.status)
    if current_status == IntegrationReplyStatus.DELIVERED:
        return
    if message_status == IntegrationMessageStatus.DELIVERED:
        reply.status = IntegrationReplyStatus.DELIVERED.value
    elif message_status == IntegrationMessageStatus.FAILED and current_status != IntegrationReplyStatus.FAILED:
        reply.status = IntegrationReplyStatus.FAILED.value
        reply.error_code = "provider_delivery_failed"
    elif (
        message_status
        in {
            IntegrationMessageStatus.QUEUED,
            IntegrationMessageStatus.SENT,
            IntegrationMessageStatus.DELIVERY_UNCONFIRMED,
        }
        and current_status != IntegrationReplyStatus.FAILED
    ):
        reply.status = IntegrationReplyStatus.PROVIDER_ACCEPTED.value
    reply.updated_at = utc_now()

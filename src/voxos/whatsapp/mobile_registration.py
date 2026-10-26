import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_core.time_context import ensure_aware_utc
from voxos.credentials.models import SecretOwner, SecretPurpose, SecretReference
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import utc_now
from voxos.integrations.inbound_sms import (
    InboundSmsEnvelope,
    InboundSmsIngressService,
)
from voxos.whatsapp.errors import (
    WhatsAppFeatureDisabledError,
    WhatsAppMobileRegistrationConflictError,
    WhatsAppMobileRegistrationNotFoundError,
)
from voxos.whatsapp.models import (
    WhatsAppLinkMethod,
    WhatsAppMobileRegistrationCandidate,
    WhatsAppMobileRegistrationChannel,
    WhatsAppMobileRegistrationState,
    WhatsAppMobileRegistrationStatus,
    WhatsAppStatusResponse,
)
from voxos.whatsapp.records import WhatsAppMobileRegistrationRecord
from voxos.whatsapp.service import WhatsAppIntegrationService

MOBILE_REGISTRATION_CAPTURE_TTL = timedelta(minutes=10)
MAX_CAPTURED_CODES = 5
MAX_VOICE_TRANSCRIPT_EVENTS = 8
VERIFICATION_CODE_PATTERN = re.compile(
    r"(?<!\d)(\d(?:[\s-]*\d){3,})(?!\d)",
)
TRANSCRIPT_TOKEN_PATTERN = re.compile(r"[a-z]+|\d", re.IGNORECASE)
SPOKEN_DIGITS = {
    "zero": "0",
    "oh": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}
ACTIVE_REGISTRATION_CONFLICT = "Finish or cancel the current WhatsApp registration before choosing another number."
REGISTRATION_CODE_MISSING = "The selected item no longer contains a verification code."
REGISTRATION_CANDIDATE_MISSING = "The selected verification code was not found."


class WhatsAppVoiceVerificationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(min_length=1, max_length=256)
    code: str = Field(pattern=r"^\d{4,8}$")
    sender: str = Field(min_length=1, max_length=32)
    received_at: datetime


class WhatsAppVoiceTranscriptEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1, max_length=256)
    call_control_id: str = Field(min_length=1, max_length=256)
    transcript: str = Field(min_length=1, max_length=512)
    received_at: datetime


class WhatsAppMobileRegistrationInbox(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    messages: tuple[InboundSmsEnvelope, ...] = Field(
        default_factory=tuple,
        max_length=MAX_CAPTURED_CODES,
    )
    voice_candidates: tuple[WhatsAppVoiceVerificationCandidate, ...] = Field(
        default_factory=tuple,
        max_length=MAX_CAPTURED_CODES,
    )
    voice_transcripts: tuple[WhatsAppVoiceTranscriptEvent, ...] = Field(
        default_factory=tuple,
        max_length=MAX_VOICE_TRANSCRIPT_EVENTS,
    )


@dataclass(frozen=True, slots=True)
class WhatsAppMobileRegistrationService:
    session: AsyncSession
    secret_vault: UserSecretVaultService

    async def start(
        self,
        *,
        user_id: str,
        source_resource_id: str,
        channel: WhatsAppMobileRegistrationChannel,
        integration_service: WhatsAppIntegrationService,
    ) -> WhatsAppMobileRegistrationStatus:
        self._require_enabled(integration_service)
        existing = await self._record_for_user(user_id=user_id, lock=True)
        if existing is not None:
            if existing.source_resource_id != source_resource_id:
                raise WhatsAppMobileRegistrationConflictError(
                    ACTIVE_REGISTRATION_CONFLICT,
                )
            inbox = await self._load_inbox(existing)
            if not inbox.messages and not inbox.voice_candidates:
                existing.state = armed_state(channel).value
                existing.capture_expires_at = utc_now() + MOBILE_REGISTRATION_CAPTURE_TTL
                existing.updated_at = utc_now()
                await self._save_inbox(
                    record=existing,
                    inbox=inbox.model_copy(update={"voice_transcripts": ()}),
                )
                await self.session.commit()
            return await self._status(existing)

        source, source_integration, phone_number = await integration_service.resolve_source_resource(
            user_id=user_id,
            source_resource_id=source_resource_id,
        )
        now = utc_now()
        record = WhatsAppMobileRegistrationRecord(
            user_id=user_id,
            source_integration_id=source_integration.id,
            source_resource_id=source.id,
            provider=source.provider,
            phone_number=phone_number,
            state=armed_state(channel).value,
            capture_expires_at=now + MOBILE_REGISTRATION_CAPTURE_TTL,
            created_at=now,
            updated_at=now,
        )
        self.session.add(record)
        await self._save_inbox(
            record=record,
            inbox=WhatsAppMobileRegistrationInbox(),
        )
        await self.session.commit()
        await self.session.refresh(record)
        return await self._status(record)

    async def get_status(
        self,
        *,
        user_id: str,
        registration_id: str,
    ) -> WhatsAppMobileRegistrationStatus:
        record = await self._record(
            user_id=user_id,
            registration_id=registration_id,
        )
        return await self._status(record)

    async def reject_candidate(
        self,
        *,
        user_id: str,
        registration_id: str,
        candidate_id: str,
        ingress: InboundSmsIngressService,
    ) -> WhatsAppMobileRegistrationStatus:
        record = await self._record(
            user_id=user_id,
            registration_id=registration_id,
            lock=True,
        )
        inbox = await self._load_inbox(record)
        voice_candidate = voice_candidate_by_id(
            inbox=inbox,
            candidate_id=candidate_id,
        )
        if voice_candidate is not None:
            remaining_voice = tuple(
                candidate for candidate in inbox.voice_candidates if candidate.candidate_id != candidate_id
            )
            record.state = (
                WhatsAppMobileRegistrationState.CODE_RECEIVED.value
                if inbox.messages or remaining_voice
                else WhatsAppMobileRegistrationState.ARMED_VOICE.value
            )
            record.capture_expires_at = utc_now() + MOBILE_REGISTRATION_CAPTURE_TTL
            record.updated_at = utc_now()
            await self._save_inbox(
                record=record,
                inbox=inbox.model_copy(
                    update={"voice_candidates": remaining_voice},
                ),
            )
            await self.session.commit()
            return await self._status(record)

        candidate = sms_candidate_by_id(inbox=inbox, candidate_id=candidate_id)
        if candidate is None:
            raise WhatsAppMobileRegistrationConflictError(
                REGISTRATION_CANDIDATE_MISSING,
            )
        await self._pause_capture(record)
        await ingress.process(candidate)
        record = await self._record(
            user_id=user_id,
            registration_id=registration_id,
            lock=True,
        )
        inbox = await self._load_inbox(record)
        remaining = tuple(message for message in inbox.messages if message.external_message_id != candidate_id)
        record.state = (
            WhatsAppMobileRegistrationState.CODE_RECEIVED.value
            if remaining or inbox.voice_candidates
            else WhatsAppMobileRegistrationState.ARMED.value
        )
        record.capture_expires_at = utc_now() + MOBILE_REGISTRATION_CAPTURE_TTL
        record.updated_at = utc_now()
        await self._save_inbox(
            record=record,
            inbox=inbox.model_copy(update={"messages": remaining}),
        )
        await self.session.commit()
        return await self._status(record)

    async def complete(
        self,
        *,
        user_id: str,
        registration_id: str,
        candidate_id: str,
        method: WhatsAppLinkMethod,
        ingress: InboundSmsIngressService,
        integration_service: WhatsAppIntegrationService,
    ) -> WhatsAppStatusResponse:
        record = await self._record(
            user_id=user_id,
            registration_id=registration_id,
            lock=True,
        )
        inbox = await self._load_inbox(record)
        selected = sms_candidate_by_id(
            inbox=inbox,
            candidate_id=candidate_id,
            required=False,
        )
        voice_candidate = voice_candidate_by_id(
            inbox=inbox,
            candidate_id=candidate_id,
        )
        selected_code = (
            extract_verification_code(selected.body)
            if selected is not None
            else (voice_candidate.code if voice_candidate is not None else None)
        )
        if selected_code is None:
            raise WhatsAppMobileRegistrationConflictError(
                REGISTRATION_CODE_MISSING,
            )

        await self._pause_capture(record)
        linked = await integration_service.start_link(
            user_id=user_id,
            source_resource_id=record.source_resource_id,
            method=method,
        )
        for message in inbox.messages:
            if selected is not None and message.external_message_id == candidate_id:
                continue
            if (
                selected is not None
                and message.from_number == selected.from_number
                and extract_verification_code(message.body) == selected_code
            ):
                continue
            await ingress.process(message)
        await self._delete(record)
        await self.session.commit()
        return linked

    async def cancel(
        self,
        *,
        user_id: str,
        registration_id: str,
        ingress: InboundSmsIngressService,
    ) -> None:
        record = await self._record(
            user_id=user_id,
            registration_id=registration_id,
            lock=True,
        )
        inbox = await self._load_inbox(record)
        await self._pause_capture(record)
        for message in inbox.messages:
            await ingress.process(message)
        await self._delete(record)
        await self.session.commit()

    async def capture(self, envelope: InboundSmsEnvelope) -> bool:
        code = extract_verification_code(envelope.body)
        if code is None:
            return False
        statement = (
            select(WhatsAppMobileRegistrationRecord)
            .where(
                col(WhatsAppMobileRegistrationRecord.user_id) == envelope.user_id,
                col(WhatsAppMobileRegistrationRecord.source_integration_id) == envelope.integration_id,
                col(WhatsAppMobileRegistrationRecord.source_resource_id) == envelope.resource_id,
                col(WhatsAppMobileRegistrationRecord.provider) == envelope.provider.value,
                col(WhatsAppMobileRegistrationRecord.capture_expires_at) > utc_now(),
            )
            .with_for_update()
        )
        record = (await self.session.exec(statement)).one_or_none()
        if record is None:
            return False
        inbox = await self._load_inbox(record)
        if any(message.external_message_id == envelope.external_message_id for message in inbox.messages):
            return True
        if len(inbox.messages) + len(inbox.voice_candidates) < MAX_CAPTURED_CODES:
            inbox = inbox.model_copy(
                update={"messages": (*inbox.messages, envelope)},
            )
            await self._save_inbox(record=record, inbox=inbox)
        record.state = WhatsAppMobileRegistrationState.CODE_RECEIVED.value
        record.updated_at = utc_now()
        await self.session.commit()
        return True

    async def active_voice_registration(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str,
    ) -> WhatsAppMobileRegistrationRecord | None:
        statement = (
            select(WhatsAppMobileRegistrationRecord)
            .where(
                col(WhatsAppMobileRegistrationRecord.user_id) == user_id,
                col(WhatsAppMobileRegistrationRecord.source_integration_id) == integration_id,
                col(WhatsAppMobileRegistrationRecord.source_resource_id) == resource_id,
                col(WhatsAppMobileRegistrationRecord.provider) == "telnyx",
                col(WhatsAppMobileRegistrationRecord.state) == WhatsAppMobileRegistrationState.ARMED_VOICE.value,
                col(WhatsAppMobileRegistrationRecord.capture_expires_at) > utc_now(),
            )
            .with_for_update()
        )
        return (await self.session.exec(statement)).one_or_none()

    async def capture_voice_transcript(
        self,
        *,
        user_id: str,
        registration_id: str,
        integration_id: str,
        resource_id: str,
        call_control_id: str,
        event_id: str,
        transcript: str,
        from_number: str | None,
        received_at: datetime,
    ) -> bool:
        cleaned_transcript = transcript.strip()
        if not cleaned_transcript:
            return False
        statement = (
            select(WhatsAppMobileRegistrationRecord)
            .where(
                col(WhatsAppMobileRegistrationRecord.id) == registration_id,
                col(WhatsAppMobileRegistrationRecord.user_id) == user_id,
                col(WhatsAppMobileRegistrationRecord.source_integration_id) == integration_id,
                col(WhatsAppMobileRegistrationRecord.source_resource_id) == resource_id,
                col(WhatsAppMobileRegistrationRecord.provider) == "telnyx",
                col(WhatsAppMobileRegistrationRecord.state) == WhatsAppMobileRegistrationState.ARMED_VOICE.value,
                col(WhatsAppMobileRegistrationRecord.capture_expires_at) > utc_now(),
            )
            .with_for_update()
        )
        record = (await self.session.exec(statement)).one_or_none()
        if record is None:
            return False
        inbox = await self._load_inbox(record)
        if any(candidate.candidate_id == event_id for candidate in inbox.voice_candidates) or any(
            event.event_id == event_id for event in inbox.voice_transcripts
        ):
            return True

        transcript_event = WhatsAppVoiceTranscriptEvent(
            event_id=event_id,
            call_control_id=call_control_id,
            transcript=cleaned_transcript[:512],
            received_at=received_at,
        )
        transcript_events = (
            *inbox.voice_transcripts,
            transcript_event,
        )[-MAX_VOICE_TRANSCRIPT_EVENTS:]
        call_transcript = " ".join(
            event.transcript for event in transcript_events if event.call_control_id == call_control_id
        )
        code = extract_spoken_verification_code(call_transcript)
        if code is None:
            await self._save_inbox(
                record=record,
                inbox=inbox.model_copy(
                    update={"voice_transcripts": transcript_events},
                ),
            )
            await self.session.commit()
            return True

        voice_candidates = inbox.voice_candidates
        if len(inbox.messages) + len(voice_candidates) < MAX_CAPTURED_CODES:
            voice_candidates = (
                *voice_candidates,
                WhatsAppVoiceVerificationCandidate(
                    candidate_id=event_id,
                    code=code,
                    sender=from_number or "Voice call",
                    received_at=received_at,
                ),
            )
        remaining_transcripts = tuple(event for event in transcript_events if event.call_control_id != call_control_id)
        record.state = WhatsAppMobileRegistrationState.CODE_RECEIVED.value
        record.updated_at = utc_now()
        await self._save_inbox(
            record=record,
            inbox=inbox.model_copy(
                update={
                    "voice_candidates": voice_candidates,
                    "voice_transcripts": remaining_transcripts,
                },
            ),
        )
        await self.session.commit()
        return True

    async def _record_for_user(
        self,
        *,
        user_id: str,
        lock: bool = False,
    ) -> WhatsAppMobileRegistrationRecord | None:
        statement = select(WhatsAppMobileRegistrationRecord).where(
            col(WhatsAppMobileRegistrationRecord.user_id) == user_id,
        )
        if lock:
            statement = statement.with_for_update()
        return (await self.session.exec(statement)).one_or_none()

    async def _record(
        self,
        *,
        user_id: str,
        registration_id: str,
        lock: bool = False,
    ) -> WhatsAppMobileRegistrationRecord:
        statement = select(WhatsAppMobileRegistrationRecord).where(
            col(WhatsAppMobileRegistrationRecord.id) == registration_id,
            col(WhatsAppMobileRegistrationRecord.user_id) == user_id,
        )
        if lock:
            statement = statement.with_for_update()
        record = (await self.session.exec(statement)).one_or_none()
        if record is None:
            raise WhatsAppMobileRegistrationNotFoundError
        return record

    async def _status(
        self,
        record: WhatsAppMobileRegistrationRecord,
    ) -> WhatsAppMobileRegistrationStatus:
        inbox = await self._load_inbox(record)
        state = (
            WhatsAppMobileRegistrationState.EXPIRED
            if ensure_aware_utc(record.capture_expires_at) <= utc_now()
            else WhatsAppMobileRegistrationState(record.state)
        )
        candidates = tuple(
            WhatsAppMobileRegistrationCandidate(
                candidate_id=message.external_message_id,
                code=code,
                channel=WhatsAppMobileRegistrationChannel.SMS,
                sender=message.from_number or "Unknown sender",
                received_at=message.received_at,
            )
            for message in inbox.messages
            if (code := extract_verification_code(message.body)) is not None
        ) + tuple(
            WhatsAppMobileRegistrationCandidate(
                candidate_id=candidate.candidate_id,
                code=candidate.code,
                channel=WhatsAppMobileRegistrationChannel.VOICE,
                sender=candidate.sender,
                received_at=candidate.received_at,
            )
            for candidate in inbox.voice_candidates
        )
        candidates = tuple(sorted(candidates, key=lambda candidate: candidate.received_at))[:MAX_CAPTURED_CODES]
        return WhatsAppMobileRegistrationStatus(
            registration_id=record.id,
            source_resource_id=record.source_resource_id,
            phone_number=record.phone_number,
            state=state,
            capture_expires_at=ensure_aware_utc(record.capture_expires_at),
            candidates=candidates,
        )

    async def _load_inbox(
        self,
        record: WhatsAppMobileRegistrationRecord,
    ) -> WhatsAppMobileRegistrationInbox:
        secret = await self.secret_vault.unseal_secret(
            session=self.session,
            reference=inbox_reference(record),
        )
        if secret is None:
            return WhatsAppMobileRegistrationInbox()
        return WhatsAppMobileRegistrationInbox.model_validate_json(
            secret.get_secret_value(),
        )

    async def _save_inbox(
        self,
        *,
        record: WhatsAppMobileRegistrationRecord,
        inbox: WhatsAppMobileRegistrationInbox,
    ) -> None:
        await self.secret_vault.seal_secret(
            session=self.session,
            secret=SecretStr(inbox.model_dump_json()),
            reference=inbox_reference(record),
        )

    async def _delete(self, record: WhatsAppMobileRegistrationRecord) -> None:
        await self.secret_vault.delete_secret(
            session=self.session,
            reference=inbox_reference(record),
        )
        await self.session.delete(record)

    async def _pause_capture(
        self,
        record: WhatsAppMobileRegistrationRecord,
    ) -> None:
        record.state = WhatsAppMobileRegistrationState.EXPIRED.value
        record.capture_expires_at = utc_now()
        record.updated_at = utc_now()
        await self.session.commit()

    def _require_enabled(
        self,
        integration_service: WhatsAppIntegrationService,
    ) -> None:
        if not integration_service.settings.whatsapp_enabled:
            raise WhatsAppFeatureDisabledError


def extract_verification_code(message: str) -> str | None:
    match = VERIFICATION_CODE_PATTERN.search(message)
    if match is None:
        return None
    code = re.sub(r"[\s-]", "", match.group(1))
    return code if 4 <= len(code) <= 8 else None


def extract_spoken_verification_code(transcript: str) -> str | None:
    numeric_code = extract_verification_code(transcript)
    if numeric_code is not None:
        return numeric_code

    best_run: tuple[str, ...] = ()
    current_run: list[str] = []
    for token in TRANSCRIPT_TOKEN_PATTERN.findall(transcript.lower()):
        digit = SPOKEN_DIGITS.get(token)
        if digit is None and len(token) == 1 and token.isdigit():
            digit = token
        if digit is not None:
            current_run.append(digit)
            continue
        if 4 <= len(current_run) <= 8 and len(current_run) > len(best_run):
            best_run = tuple(current_run)
        current_run = []
    if 4 <= len(current_run) <= 8 and len(current_run) > len(best_run):
        best_run = tuple(current_run)
    return "".join(best_run) if best_run else None


def sms_candidate_by_id(
    *,
    inbox: WhatsAppMobileRegistrationInbox,
    candidate_id: str,
    required: bool = True,
) -> InboundSmsEnvelope | None:
    for candidate in inbox.messages:
        if candidate.external_message_id == candidate_id:
            return candidate
    if not required:
        return None
    raise WhatsAppMobileRegistrationConflictError(
        REGISTRATION_CANDIDATE_MISSING,
    )


def voice_candidate_by_id(
    *,
    inbox: WhatsAppMobileRegistrationInbox,
    candidate_id: str,
) -> WhatsAppVoiceVerificationCandidate | None:
    for candidate in inbox.voice_candidates:
        if candidate.candidate_id == candidate_id:
            return candidate
    return None


def armed_state(
    channel: WhatsAppMobileRegistrationChannel,
) -> WhatsAppMobileRegistrationState:
    if channel == WhatsAppMobileRegistrationChannel.VOICE:
        return WhatsAppMobileRegistrationState.ARMED_VOICE
    return WhatsAppMobileRegistrationState.ARMED


def inbox_reference(
    record: WhatsAppMobileRegistrationRecord,
) -> SecretReference:
    return SecretReference(
        owner=SecretOwner(user_id=record.user_id),
        provider="whatsapp",
        credential_id=record.id,
        purpose=SecretPurpose.WHATSAPP_MOBILE_REGISTRATION_INBOX,
    )

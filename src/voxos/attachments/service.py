import asyncio
import base64
import logging
import re
import time
import warnings
from collections.abc import Sequence
from datetime import timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import PurePath
from typing import Protocol, cast

from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_runtime.models import (
    AgentTranscriptItem,
    AgentTranscriptRole,
    AgentTranscriptSource,
)
from voxos.agent_runtime.records import (
    AgentRunRecord,
    AgentSessionRecord,
    AgentTranscriptRecord,
)
from voxos.attachments.models import (
    AttachmentClientSource,
    AttachmentState,
    AttachmentSummary,
    AttachmentVariant,
    ClaimedTurn,
    ImageMimeType,
    NormalizedImage,
)
from voxos.attachments.records import MessageAttachmentRecord
from voxos.attachments.settings import AttachmentSettings
from voxos.attachments.storage import AttachmentObjectStore
from voxos.attachments.telemetry import (
    record_storage_latency,
    record_upload_success,
    record_validation_failure,
)
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.notifications.outbox import enqueue_chat_message_notification

logger = logging.getLogger(__name__)
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")
_READ_CHUNK_SIZE = 64 * 1024
_JPEG_QUALITY = 88
_SUPPORTED_FORMATS = frozenset({"JPEG", "PNG", "HEIF", "HEIC"})
_ATTACHMENT_NOT_FOUND = "Attachment was not found"
_ATTACHMENT_SET_NOT_FOUND = "One or more attachments were not found"
_ATTACHMENT_SET_NOT_READY = "One or more attachments are not ready"
_ATTACHMENT_SET_DUPLICATE = "Attachment IDs must be unique"
_ATTACHMENT_SET_TOO_LARGE = "The selected images exceed the per-message limit"
_CHAT_NOT_FOUND = "Chat was not found"
_EMPTY_UPLOAD = "Image upload was empty"
_IDEMPOTENCY_CONFLICT = "The turn idempotency key was already used with different content"
_IMAGE_DECODE_FAILED = "Image could not be decoded safely"
_IMAGE_FORMAT_INVALID = "Supported image formats are JPEG, PNG, and HEIC"
_IMAGE_ANIMATED = "Animated images are not supported"
_IMAGE_PIXELS_EXCEEDED = "Image pixel count exceeds the limit"
_NORMALIZED_IMAGE_TOO_LARGE = "Normalized image exceeds the size limit"
_STORED_IMAGE_INVALID = "Stored attachment content is invalid"
_TURN_ALREADY_SUBMITTED = "This chat turn was already submitted"
_UPLOAD_TOO_LARGE = "Image exceeds the upload size limit"

register_heif_opener()


class AsyncAttachmentUpload(Protocol):
    filename: str | None

    async def read(self, size: int = -1) -> bytes:
        """Read upload bytes."""


class AttachmentError(RuntimeError):
    """Base class for safe attachment failures."""


class AttachmentNotFoundError(AttachmentError):
    """Raised when an attachment is absent or not owned by the caller."""


class AttachmentConflictError(AttachmentError):
    """Raised when attachment state cannot satisfy the requested operation."""


class AttachmentValidationError(AttachmentError):
    """Raised when uploaded or selected media violates the attachment contract."""

    def __init__(self, message: str, *, code: str = "validation_failed") -> None:
        self.code = code
        super().__init__(message)


class AttachmentModelUnsupportedError(AttachmentError):
    """Raised when a selected chat model cannot accept images."""


class AttachmentService:
    def __init__(
        self,
        *,
        session_factory: AsyncSessionFactory,
        object_store: AttachmentObjectStore,
        settings: AttachmentSettings,
        chat_notifications_enabled: bool = False,
        chat_notification_max_attempts: int = 5,
    ) -> None:
        self._session_factory = session_factory
        self._object_store = object_store
        self._chat_notifications_enabled = chat_notifications_enabled
        self._chat_notification_max_attempts = chat_notification_max_attempts
        self.settings = settings

    async def upload(
        self,
        *,
        user_id: str,
        chat_id: str,
        client_draft_id: str,
        client_source: AttachmentClientSource,
        upload: AsyncAttachmentUpload,
    ) -> MessageAttachmentRecord:
        upload_started = time.perf_counter()
        try:
            await self._require_chat(user_id=user_id, chat_id=chat_id)
            await self.cleanup_stale(user_id=user_id)
            await self.retry_pending_deletions(user_id=user_id)
            source = await self._read_upload(upload)
            normalized = await asyncio.to_thread(
                normalize_image,
                source,
                self.settings,
            )
        except AttachmentValidationError as exc:
            record_validation_failure(
                client_source=client_source.value,
                code=exc.code,
            )
            raise
        record = MessageAttachmentRecord(
            user_id=user_id,
            chat_id=chat_id,
            client_draft_id=client_draft_id,
            client_source=client_source.value,
            state=AttachmentState.PENDING.value,
            display_name=safe_display_name(upload.filename),
            mime_type=normalized.mime_type,
            byte_size=normalized.byte_size,
            sha256=normalized.sha256,
            width=normalized.width,
            height=normalized.height,
            object_key="pending",
            thumbnail_key="pending",
        )
        prefix = self.settings.s3_prefix
        tenant_prefix = sha256(user_id.encode()).hexdigest()[:32]
        record.object_key = f"{prefix}/{tenant_prefix}/{record.id}/image"
        record.thumbnail_key = f"{prefix}/{tenant_prefix}/{record.id}/thumbnail"

        async with self._session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)

        storage_started = time.perf_counter()
        storage_error: Exception | None = None
        stored: MessageAttachmentRecord | None = None
        async with self._session_factory() as session:
            stored = await self._get_record(
                session=session,
                attachment_id=record.id,
                user_id=user_id,
                chat_id=chat_id,
                for_update=True,
            )
            if stored is not None and stored.state == AttachmentState.PENDING.value:
                try:
                    await self._object_store.put(
                        stored.object_key,
                        normalized.content,
                        normalized.mime_type,
                    )
                    await self._object_store.put(
                        stored.thumbnail_key,
                        normalized.thumbnail,
                        normalized.mime_type,
                    )
                except Exception as exc:
                    storage_error = exc
                    now = utc_now()
                    stored.state = AttachmentState.DELETED.value
                    stored.deleted_at = now
                    stored.storage_deleted_at = None
                    stored.updated_at = now
                else:
                    stored.state = AttachmentState.READY.value
                    stored.updated_at = utc_now()
                session.add(stored)
                await session.commit()
                await session.refresh(stored)
        record_storage_latency(
            operation="Put",
            duration_ms=(time.perf_counter() - storage_started) * 1000,
        )

        if storage_error is not None:
            if stored is None:
                await self._delete_objects(record)
                raise storage_error
            await self._delete_objects(stored)
            raise storage_error
        if stored is None or stored.state != AttachmentState.READY.value:
            await self._delete_objects(record)
            raise AttachmentNotFoundError(_ATTACHMENT_NOT_FOUND)
        record_upload_success(
            client_source=client_source.value,
            normalized_bytes=stored.byte_size,
            duration_ms=(time.perf_counter() - upload_started) * 1000,
        )
        return stored

    async def validate_ready_set(
        self,
        *,
        user_id: str,
        chat_id: str,
        attachment_ids: Sequence[str],
    ) -> tuple[MessageAttachmentRecord, ...]:
        if len(attachment_ids) > self.settings.max_images_per_turn:
            message = f"A message can include at most {self.settings.max_images_per_turn} images"
            raise AttachmentValidationError(message)
        if len(set(attachment_ids)) != len(attachment_ids):
            raise AttachmentValidationError(_ATTACHMENT_SET_DUPLICATE)
        if not attachment_ids:
            return ()
        async with self._session_factory() as session:
            records = await self._records_by_ids(
                session=session,
                user_id=user_id,
                chat_id=chat_id,
                attachment_ids=attachment_ids,
            )
        if len(records) != len(attachment_ids):
            raise AttachmentNotFoundError(_ATTACHMENT_SET_NOT_FOUND)
        by_id = {record.id: record for record in records}
        ordered = tuple(by_id[attachment_id] for attachment_id in attachment_ids)
        if any(record.state != AttachmentState.READY.value for record in ordered):
            raise AttachmentConflictError(_ATTACHMENT_SET_NOT_READY)
        if sum(record.byte_size for record in ordered) > self.settings.max_turn_bytes:
            raise AttachmentValidationError(_ATTACHMENT_SET_TOO_LARGE)
        return ordered

    async def claim_turn(
        self,
        *,
        user_id: str,
        chat_id: str,
        agent_id: str,
        text: str,
        attachment_ids: Sequence[str],
        client_turn_id: str,
    ) -> ClaimedTurn:
        async with self._session_factory() as session:
            existing = await self._existing_turn_record(
                session=session,
                user_id=user_id,
                chat_id=chat_id,
                client_turn_id=client_turn_id,
            )
            if existing is not None:
                await self._validate_existing_turn(
                    session=session,
                    transcript=existing,
                    text=text,
                    attachment_ids=attachment_ids,
                )
                return ClaimedTurn(transcript_id=existing.id, created=False)

            records = await self._records_by_ids(
                session=session,
                user_id=user_id,
                chat_id=chat_id,
                attachment_ids=attachment_ids,
                for_update=True,
            )
            existing = await self._existing_turn_record(
                session=session,
                user_id=user_id,
                chat_id=chat_id,
                client_turn_id=client_turn_id,
            )
            if existing is not None:
                await self._validate_existing_turn(
                    session=session,
                    transcript=existing,
                    text=text,
                    attachment_ids=attachment_ids,
                )
                return ClaimedTurn(transcript_id=existing.id, created=False)
            ordered = self._validate_claim_records(
                records=records,
                attachment_ids=attachment_ids,
            )
            transcript = AgentTranscriptRecord(
                session_id=chat_id,
                user_id=user_id,
                agent_id=agent_id,
                role=AgentTranscriptRole.USER.value,
                text=text,
                source=AgentTranscriptSource.TEXT_INPUT.value,
                client_turn_id=client_turn_id,
                created_at=utc_now(),
            )
            session.add(transcript)
            await session.flush()
            now = utc_now()
            for position, record in enumerate(ordered):
                record.transcript_id = transcript.id
                record.position = position
                record.state = AttachmentState.ATTACHED.value
                record.updated_at = now
                session.add(record)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                existing = await self._existing_turn_record(
                    session=session,
                    user_id=user_id,
                    chat_id=chat_id,
                    client_turn_id=client_turn_id,
                )
                if existing is not None:
                    await self._validate_existing_turn(
                        session=session,
                        transcript=existing,
                        text=text,
                        attachment_ids=attachment_ids,
                    )
                    return ClaimedTurn(
                        transcript_id=existing.id,
                        created=False,
                    )
                raise AttachmentConflictError(_TURN_ALREADY_SUBMITTED) from exc
            return ClaimedTurn(transcript_id=transcript.id, created=True)

    async def existing_turn(
        self,
        *,
        user_id: str,
        chat_id: str,
        text: str,
        attachment_ids: Sequence[str],
        client_turn_id: str,
    ) -> AgentTranscriptRecord | None:
        async with self._session_factory() as session:
            transcript = await self._existing_turn_record(
                session=session,
                user_id=user_id,
                chat_id=chat_id,
                client_turn_id=client_turn_id,
            )
            if transcript is None:
                return None
            await self._validate_existing_turn(
                session=session,
                transcript=transcript,
                text=text,
                attachment_ids=attachment_ids,
            )
            return transcript

    async def existing_generated_turn(
        self,
        *,
        user_id: str,
        chat_id: str,
        client_turn_id: str,
        prompt: str,
        source_attachment_ids: Sequence[str],
    ) -> (
        tuple[
            AgentTranscriptRecord,
            AgentTranscriptRecord,
            MessageAttachmentRecord,
        ]
        | None
    ):
        async with self._session_factory() as session:
            user_message = await self._existing_turn_record(
                session=session,
                user_id=user_id,
                chat_id=chat_id,
                client_turn_id=client_turn_id,
            )
            assistant_message = await self._existing_turn_record(
                session=session,
                user_id=user_id,
                chat_id=chat_id,
                client_turn_id=_generated_output_client_turn_id(client_turn_id),
            )
            if user_message is None or assistant_message is None:
                return None
            await self._validate_existing_turn(
                session=session,
                transcript=user_message,
                text=prompt,
                attachment_ids=source_attachment_ids,
            )
            result = await session.exec(
                select(MessageAttachmentRecord).where(
                    col(MessageAttachmentRecord.user_id) == user_id,
                    col(MessageAttachmentRecord.chat_id) == chat_id,
                    col(MessageAttachmentRecord.transcript_id) == assistant_message.id,
                    col(MessageAttachmentRecord.state) == AttachmentState.ATTACHED.value,
                ),
            )
            attachment = result.one_or_none()
            if attachment is None:
                return None
            return user_message, assistant_message, attachment

    async def persist_generated_turn(
        self,
        *,
        user_id: str,
        chat_id: str,
        agent_id: str,
        prompt: str,
        source_attachment_ids: Sequence[str],
        client_turn_id: str,
        client_source: AttachmentClientSource,
        generated_content: bytes,
        assistant_text: str,
    ) -> tuple[
        AgentTranscriptRecord,
        AgentTranscriptRecord,
        MessageAttachmentRecord,
    ]:
        existing = await self.existing_generated_turn(
            user_id=user_id,
            chat_id=chat_id,
            client_turn_id=client_turn_id,
            prompt=prompt,
            source_attachment_ids=source_attachment_ids,
        )
        if existing is not None:
            return existing
        normalized = await asyncio.to_thread(
            normalize_image,
            generated_content,
            self.settings,
        )
        output_record: MessageAttachmentRecord | None = None
        try:
            async with self._session_factory() as session:
                chat = await self._active_chat(
                    session=session,
                    user_id=user_id,
                    chat_id=chat_id,
                )
                if chat is None or chat.agent_id != agent_id:
                    raise AttachmentNotFoundError(_CHAT_NOT_FOUND)
                source_records = await self._records_by_ids(
                    session=session,
                    user_id=user_id,
                    chat_id=chat_id,
                    attachment_ids=source_attachment_ids,
                    for_update=True,
                )
                ordered_sources = self._validate_claim_records(
                    records=source_records,
                    attachment_ids=source_attachment_ids,
                )
                existing_user = await self._existing_turn_record(
                    session=session,
                    user_id=user_id,
                    chat_id=chat_id,
                    client_turn_id=client_turn_id,
                )
                existing_assistant = await self._existing_turn_record(
                    session=session,
                    user_id=user_id,
                    chat_id=chat_id,
                    client_turn_id=_generated_output_client_turn_id(client_turn_id),
                )
                if existing_user is not None or existing_assistant is not None:
                    raise AttachmentConflictError(_TURN_ALREADY_SUBMITTED)

                now = utc_now()
                user_message = AgentTranscriptRecord(
                    session_id=chat_id,
                    user_id=user_id,
                    agent_id=agent_id,
                    role=AgentTranscriptRole.USER.value,
                    text=prompt,
                    source=AgentTranscriptSource.TEXT_INPUT.value,
                    client_turn_id=client_turn_id,
                    created_at=now,
                )
                assistant_message = AgentTranscriptRecord(
                    session_id=chat_id,
                    user_id=user_id,
                    agent_id=agent_id,
                    role=AgentTranscriptRole.ASSISTANT.value,
                    text=assistant_text,
                    source=AgentTranscriptSource.TEXT_OUTPUT.value,
                    client_turn_id=_generated_output_client_turn_id(client_turn_id),
                    created_at=now,
                )
                session.add(user_message)
                session.add(assistant_message)
                await session.flush()

                output_record = MessageAttachmentRecord(
                    user_id=user_id,
                    chat_id=chat_id,
                    transcript_id=assistant_message.id,
                    client_draft_id=client_turn_id,
                    client_source=client_source.value,
                    position=0,
                    state=AttachmentState.ATTACHED.value,
                    display_name=(
                        "generated-image.png" if normalized.mime_type == "image/png" else "generated-image.jpg"
                    ),
                    mime_type=normalized.mime_type,
                    byte_size=normalized.byte_size,
                    sha256=normalized.sha256,
                    width=normalized.width,
                    height=normalized.height,
                    object_key="pending",
                    thumbnail_key="pending",
                    created_at=now,
                    updated_at=now,
                )
                tenant_prefix = sha256(user_id.encode()).hexdigest()[:32]
                prefix = self.settings.s3_prefix
                output_record.object_key = f"{prefix}/{tenant_prefix}/{output_record.id}/image"
                output_record.thumbnail_key = f"{prefix}/{tenant_prefix}/{output_record.id}/thumbnail"
                await self._object_store.put(
                    output_record.object_key,
                    normalized.content,
                    normalized.mime_type,
                )
                await self._object_store.put(
                    output_record.thumbnail_key,
                    normalized.thumbnail,
                    normalized.mime_type,
                )
                for position, source_record in enumerate(ordered_sources):
                    source_record.transcript_id = user_message.id
                    source_record.position = position
                    source_record.state = AttachmentState.ATTACHED.value
                    source_record.updated_at = now
                    session.add(source_record)
                chat.updated_at = now
                session.add(chat)
                session.add(output_record)
                if self._chat_notifications_enabled:
                    await enqueue_chat_message_notification(
                        session=session,
                        item=AgentTranscriptItem(
                            session_id=chat_id,
                            user_id=user_id,
                            agent_id=agent_id,
                            role=AgentTranscriptRole.ASSISTANT,
                            text=assistant_text,
                            source=AgentTranscriptSource.TEXT_OUTPUT,
                            client_turn_id=assistant_message.client_turn_id,
                            created_at=now,
                        ),
                        transcript_id=assistant_message.id,
                        max_attempts=self._chat_notification_max_attempts,
                    )
                await session.commit()
                await session.refresh(user_message)
                await session.refresh(assistant_message)
                await session.refresh(output_record)
                return user_message, assistant_message, output_record
        except IntegrityError as exc:
            if output_record is not None:
                await self._delete_objects(output_record)
            existing = await self.existing_generated_turn(
                user_id=user_id,
                chat_id=chat_id,
                client_turn_id=client_turn_id,
                prompt=prompt,
                source_attachment_ids=source_attachment_ids,
            )
            if existing is not None:
                return existing
            raise AttachmentConflictError(_TURN_ALREADY_SUBMITTED) from exc
        except Exception:
            if output_record is not None:
                await self._delete_objects(output_record)
            raise

    async def tool_edit_reference_ids(
        self,
        *,
        user_id: str,
        chat_id: str,
        agent_id: str,
        run_id: str,
    ) -> tuple[str, ...]:
        """Resolve edit inputs from trusted current-turn or transcript state."""

        async with self._session_factory() as session:
            run_result = await session.exec(
                select(AgentRunRecord).where(
                    col(AgentRunRecord.id) == run_id,
                    col(AgentRunRecord.user_id) == user_id,
                    col(AgentRunRecord.agent_id) == agent_id,
                    col(AgentRunRecord.session_id) == chat_id,
                    col(AgentRunRecord.chat_id) == chat_id,
                ),
            )
            run = run_result.one_or_none()
            if run is None:
                raise AttachmentNotFoundError(_CHAT_NOT_FOUND)

            client_turn_id = run.payload_json.get("client_turn_id")
            if isinstance(client_turn_id, str) and client_turn_id:
                transcript_result = await session.exec(
                    select(AgentTranscriptRecord).where(
                        col(AgentTranscriptRecord.user_id) == user_id,
                        col(AgentTranscriptRecord.session_id) == chat_id,
                        col(AgentTranscriptRecord.agent_id) == agent_id,
                        col(AgentTranscriptRecord.role) == AgentTranscriptRole.USER.value,
                        col(AgentTranscriptRecord.client_turn_id) == client_turn_id,
                    ),
                )
                current_turn = transcript_result.one_or_none()
                if current_turn is not None:
                    current_result = await session.exec(
                        select(MessageAttachmentRecord)
                        .where(
                            col(MessageAttachmentRecord.user_id) == user_id,
                            col(MessageAttachmentRecord.chat_id) == chat_id,
                            col(MessageAttachmentRecord.transcript_id) == current_turn.id,
                            col(MessageAttachmentRecord.state) == AttachmentState.ATTACHED.value,
                        )
                        .order_by(col(MessageAttachmentRecord.position)),
                    )
                    current_records = tuple(current_result.all())
                    if current_records:
                        return tuple(record.id for record in current_records)

            assistant_result = await session.exec(
                select(AgentTranscriptRecord)
                .join(
                    MessageAttachmentRecord,
                    col(MessageAttachmentRecord.transcript_id) == AgentTranscriptRecord.id,
                )
                .where(
                    col(AgentTranscriptRecord.user_id) == user_id,
                    col(AgentTranscriptRecord.session_id) == chat_id,
                    col(AgentTranscriptRecord.agent_id) == agent_id,
                    col(AgentTranscriptRecord.role) == AgentTranscriptRole.ASSISTANT.value,
                    col(MessageAttachmentRecord.user_id) == user_id,
                    col(MessageAttachmentRecord.chat_id) == chat_id,
                    col(MessageAttachmentRecord.state) == AttachmentState.ATTACHED.value,
                )
                .order_by(
                    col(AgentTranscriptRecord.created_at).desc(),
                    col(AgentTranscriptRecord.id).desc(),
                )
                .limit(1),
            )
            latest_assistant = assistant_result.one_or_none()
            if latest_assistant is None:
                return ()
            latest_result = await session.exec(
                select(MessageAttachmentRecord)
                .where(
                    col(MessageAttachmentRecord.user_id) == user_id,
                    col(MessageAttachmentRecord.chat_id) == chat_id,
                    col(MessageAttachmentRecord.transcript_id) == latest_assistant.id,
                    col(MessageAttachmentRecord.state) == AttachmentState.ATTACHED.value,
                )
                .order_by(col(MessageAttachmentRecord.position)),
            )
            latest_records = tuple(latest_result.all())
            return tuple(record.id for record in latest_records[:1])

    async def existing_staged_generated_attachment(
        self,
        *,
        user_id: str,
        chat_id: str,
        client_draft_id: str,
    ) -> MessageAttachmentRecord | None:
        async with self._session_factory() as session:
            result = await session.exec(
                select(MessageAttachmentRecord).where(
                    col(MessageAttachmentRecord.user_id) == user_id,
                    col(MessageAttachmentRecord.chat_id) == chat_id,
                    col(MessageAttachmentRecord.client_draft_id) == client_draft_id,
                    col(MessageAttachmentRecord.client_source) == AttachmentClientSource.AGENT.value,
                    col(MessageAttachmentRecord.state).in_(
                        [
                            AttachmentState.READY.value,
                            AttachmentState.ATTACHED.value,
                        ],
                    ),
                ),
            )
            return result.first()

    async def stage_generated_attachment(
        self,
        *,
        user_id: str,
        chat_id: str,
        agent_id: str,
        client_draft_id: str,
        generated_content: bytes,
    ) -> MessageAttachmentRecord:
        existing = await self.existing_staged_generated_attachment(
            user_id=user_id,
            chat_id=chat_id,
            client_draft_id=client_draft_id,
        )
        if existing is not None:
            return existing

        normalized = await asyncio.to_thread(
            normalize_image,
            generated_content,
            self.settings,
        )
        now = utc_now()
        record = MessageAttachmentRecord(
            user_id=user_id,
            chat_id=chat_id,
            client_draft_id=client_draft_id,
            client_source=AttachmentClientSource.AGENT.value,
            state=AttachmentState.READY.value,
            display_name=("generated-image.png" if normalized.mime_type == "image/png" else "generated-image.jpg"),
            mime_type=normalized.mime_type,
            byte_size=normalized.byte_size,
            sha256=normalized.sha256,
            width=normalized.width,
            height=normalized.height,
            object_key="pending",
            thumbnail_key="pending",
            created_at=now,
            updated_at=now,
        )
        tenant_prefix = sha256(user_id.encode()).hexdigest()[:32]
        prefix = self.settings.s3_prefix
        record.object_key = f"{prefix}/{tenant_prefix}/{record.id}/image"
        record.thumbnail_key = f"{prefix}/{tenant_prefix}/{record.id}/thumbnail"
        try:
            async with self._session_factory() as session:
                chat = await self._active_chat(
                    session=session,
                    user_id=user_id,
                    chat_id=chat_id,
                )
                if chat is None or chat.agent_id != agent_id:
                    raise AttachmentNotFoundError(_CHAT_NOT_FOUND)
                await self._object_store.put(
                    record.object_key,
                    normalized.content,
                    normalized.mime_type,
                )
                await self._object_store.put(
                    record.thumbnail_key,
                    normalized.thumbnail,
                    normalized.mime_type,
                )
                session.add(record)
                await session.commit()
                await session.refresh(record)
        except Exception:
            await self._delete_objects(record)
            raise
        return record

    async def persist_assistant_with_artifacts(
        self,
        *,
        user_id: str,
        chat_id: str,
        agent_id: str,
        text: str,
        source: AgentTranscriptSource,
        attachment_ids: Sequence[str],
        notification_run_id: str | None = None,
        interrupted: bool = False,
    ) -> AgentTranscriptRecord:
        if not attachment_ids or len(set(attachment_ids)) != len(attachment_ids):
            raise AttachmentValidationError(_ATTACHMENT_SET_DUPLICATE)
        async with self._session_factory() as session:
            chat = await self._active_chat(
                session=session,
                user_id=user_id,
                chat_id=chat_id,
            )
            if chat is None or chat.agent_id != agent_id:
                raise AttachmentNotFoundError(_CHAT_NOT_FOUND)
            records = await self._records_by_ids(
                session=session,
                user_id=user_id,
                chat_id=chat_id,
                attachment_ids=attachment_ids,
                for_update=True,
            )
            if len(records) != len(attachment_ids):
                raise AttachmentNotFoundError(_ATTACHMENT_SET_NOT_FOUND)
            by_id = {record.id: record for record in records}
            ordered = tuple(by_id[attachment_id] for attachment_id in attachment_ids)
            if any(record.client_source != AttachmentClientSource.AGENT.value for record in ordered):
                raise AttachmentNotFoundError(_ATTACHMENT_SET_NOT_FOUND)

            transcript_ids = {record.transcript_id for record in ordered}
            if all(record.state == AttachmentState.ATTACHED.value for record in ordered):
                if len(transcript_ids) != 1 or None in transcript_ids:
                    raise AttachmentConflictError(_TURN_ALREADY_SUBMITTED)
                transcript_id = next(iter(transcript_ids))
                result = await session.exec(
                    select(AgentTranscriptRecord).where(
                        col(AgentTranscriptRecord.id) == transcript_id,
                        col(AgentTranscriptRecord.user_id) == user_id,
                        col(AgentTranscriptRecord.session_id) == chat_id,
                        col(AgentTranscriptRecord.agent_id) == agent_id,
                    ),
                )
                existing = result.one_or_none()
                if existing is None:
                    raise AttachmentConflictError(_TURN_ALREADY_SUBMITTED)
                return existing
            if any(record.state != AttachmentState.READY.value for record in ordered):
                raise AttachmentConflictError(_ATTACHMENT_SET_NOT_READY)

            now = utc_now()
            transcript = AgentTranscriptRecord(
                session_id=chat_id,
                user_id=user_id,
                agent_id=agent_id,
                role=AgentTranscriptRole.ASSISTANT.value,
                text=text,
                source=source.value,
                interrupted=interrupted,
                is_final=not interrupted,
                created_at=now,
            )
            session.add(transcript)
            await session.flush()
            for position, record in enumerate(ordered):
                record.transcript_id = transcript.id
                record.position = position
                record.state = AttachmentState.ATTACHED.value
                record.updated_at = now
                session.add(record)
            chat.updated_at = now
            session.add(chat)
            if self._chat_notifications_enabled:
                await enqueue_chat_message_notification(
                    session=session,
                    item=AgentTranscriptItem(
                        session_id=chat_id,
                        user_id=user_id,
                        agent_id=agent_id,
                        role=AgentTranscriptRole.ASSISTANT,
                        text=text,
                        source=source,
                        interrupted=interrupted,
                        is_final=not interrupted,
                        notification_run_id=notification_run_id,
                        created_at=now,
                    ),
                    transcript_id=transcript.id,
                    max_attempts=self._chat_notification_max_attempts,
                )
            await session.commit()
            await session.refresh(transcript)
            return transcript

    async def get_content(
        self,
        *,
        user_id: str,
        chat_id: str,
        attachment_id: str,
        variant: AttachmentVariant,
    ) -> tuple[bytes, str]:
        async with self._session_factory() as session:
            chat = await self._active_chat(
                session=session,
                user_id=user_id,
                chat_id=chat_id,
                read_lock=True,
            )
            if chat is None:
                raise AttachmentNotFoundError(_ATTACHMENT_NOT_FOUND)
            record = await self._get_record(
                session=session,
                attachment_id=attachment_id,
                user_id=user_id,
                chat_id=chat_id,
                read_lock=True,
            )
            if record is None or record.state not in {
                AttachmentState.READY.value,
                AttachmentState.ATTACHED.value,
            }:
                raise AttachmentNotFoundError(_ATTACHMENT_NOT_FOUND)
            key = record.thumbnail_key if variant == AttachmentVariant.THUMBNAIL else record.object_key
            mime_type = record.mime_type
            storage_started = time.perf_counter()
            try:
                content = await self._object_store.get(key)
            except FileNotFoundError as exc:
                raise AttachmentNotFoundError(_ATTACHMENT_NOT_FOUND) from exc
            finally:
                record_storage_latency(
                    operation="Get",
                    duration_ms=(time.perf_counter() - storage_started) * 1000,
                )
        if not content or len(content) > self.settings.max_image_bytes:
            raise AttachmentValidationError(_STORED_IMAGE_INVALID)
        return content, mime_type

    async def delete_staged(
        self,
        *,
        user_id: str,
        chat_id: str,
        attachment_id: str,
    ) -> None:
        async with self._session_factory() as session:
            record = await self._get_record(
                session=session,
                attachment_id=attachment_id,
                user_id=user_id,
                chat_id=chat_id,
                for_update=True,
            )
            if record is None or record.state not in {
                AttachmentState.PENDING.value,
                AttachmentState.READY.value,
                AttachmentState.FAILED.value,
            }:
                raise AttachmentNotFoundError(_ATTACHMENT_NOT_FOUND)
            record.state = AttachmentState.DELETED.value
            record.deleted_at = utc_now()
            record.storage_deleted_at = None
            record.updated_at = record.deleted_at
            session.add(record)
            await session.commit()
        await self._delete_objects(record)

    async def delete_chat(self, *, user_id: str, chat_id: str) -> None:
        async with self._session_factory() as session:
            result = await session.exec(
                select(MessageAttachmentRecord)
                .where(
                    col(MessageAttachmentRecord.user_id) == user_id,
                    col(MessageAttachmentRecord.chat_id) == chat_id,
                    col(MessageAttachmentRecord.state) != AttachmentState.DELETED.value,
                )
                .order_by(col(MessageAttachmentRecord.id))
                .with_for_update(),
            )
            records = tuple(result.all())
            now = utc_now()
            for record in records:
                record.state = AttachmentState.DELETED.value
                record.deleted_at = now
                record.storage_deleted_at = None
                record.updated_at = now
                session.add(record)
            await session.commit()
        for record in records:
            await self._delete_objects(record)

    async def cleanup_stale(self, *, user_id: str) -> int:
        cutoff = utc_now() - timedelta(seconds=self.settings.staging_ttl_seconds)
        async with self._session_factory() as session:
            result = await session.exec(
                select(MessageAttachmentRecord)
                .where(
                    col(MessageAttachmentRecord.user_id) == user_id,
                    col(MessageAttachmentRecord.state).in_(
                        [
                            AttachmentState.PENDING.value,
                            AttachmentState.READY.value,
                            AttachmentState.FAILED.value,
                        ],
                    ),
                    col(MessageAttachmentRecord.created_at) < cutoff,
                )
                .order_by(col(MessageAttachmentRecord.id))
                .with_for_update(skip_locked=True),
            )
            records = tuple(result.all())
            now = utc_now()
            for record in records:
                record.state = AttachmentState.DELETED.value
                record.deleted_at = now
                record.storage_deleted_at = None
                record.updated_at = now
                session.add(record)
            await session.commit()
        for record in records:
            await self._delete_objects(record)
        return len(records)

    async def retry_pending_deletions(self, *, user_id: str) -> int:
        async with self._session_factory() as session:
            result = await session.exec(
                select(MessageAttachmentRecord)
                .where(
                    col(MessageAttachmentRecord.user_id) == user_id,
                    col(MessageAttachmentRecord.state) == AttachmentState.DELETED.value,
                    col(MessageAttachmentRecord.storage_deleted_at).is_(None),
                    col(MessageAttachmentRecord.deletion_attempts) < self.settings.max_deletion_attempts,
                )
                .limit(100),
            )
            records = tuple(result.all())
        for record in records:
            await self._delete_objects(record)
        return len(records)

    async def list_for_transcripts(
        self,
        *,
        user_id: str,
        chat_id: str,
        transcript_ids: Sequence[str],
    ) -> dict[str, tuple[MessageAttachmentRecord, ...]]:
        if not transcript_ids:
            return {}
        async with self._session_factory() as session:
            result = await session.exec(
                select(MessageAttachmentRecord)
                .where(
                    col(MessageAttachmentRecord.user_id) == user_id,
                    col(MessageAttachmentRecord.chat_id) == chat_id,
                    col(MessageAttachmentRecord.transcript_id).in_(transcript_ids),
                    col(MessageAttachmentRecord.state) == AttachmentState.ATTACHED.value,
                )
                .order_by(
                    col(MessageAttachmentRecord.transcript_id),
                    col(MessageAttachmentRecord.position),
                ),
            )
            records = result.all()
        grouped: dict[str, list[MessageAttachmentRecord]] = {}
        for record in records:
            if record.transcript_id is None:
                continue
            grouped.setdefault(record.transcript_id, []).append(record)
        return {transcript_id: tuple(transcript_records) for transcript_id, transcript_records in grouped.items()}

    async def image_data_url(self, record: MessageAttachmentRecord) -> str:
        if record.state != AttachmentState.ATTACHED.value:
            raise AttachmentNotFoundError(_ATTACHMENT_NOT_FOUND)
        content, _ = await self.get_content(
            user_id=record.user_id,
            chat_id=record.chat_id,
            attachment_id=record.id,
            variant=AttachmentVariant.FULL,
        )
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{record.mime_type};base64,{encoded}"

    async def summaries_for_transcripts(
        self,
        *,
        user_id: str,
        chat_id: str,
        transcript_ids: Sequence[str],
    ) -> dict[str, tuple[AttachmentSummary, ...]]:
        grouped = await self.list_for_transcripts(
            user_id=user_id,
            chat_id=chat_id,
            transcript_ids=transcript_ids,
        )
        return {
            transcript_id: tuple(attachment_summary(record) for record in records)
            for transcript_id, records in grouped.items()
        }

    async def _read_upload(self, upload: AsyncAttachmentUpload) -> bytes:
        parts: list[bytes] = []
        total = 0
        while True:
            chunk = await upload.read(_READ_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > self.settings.max_source_bytes:
                raise AttachmentValidationError(
                    _UPLOAD_TOO_LARGE,
                    code="source_bytes_exceeded",
                )
            parts.append(chunk)
        if total == 0:
            raise AttachmentValidationError(
                _EMPTY_UPLOAD,
                code="empty_upload",
            )
        return b"".join(parts)

    async def _require_chat(self, *, user_id: str, chat_id: str) -> None:
        async with self._session_factory() as session:
            chat = await self._active_chat(
                session=session,
                user_id=user_id,
                chat_id=chat_id,
            )
            if chat is None:
                raise AttachmentNotFoundError(_CHAT_NOT_FOUND)

    async def _delete_objects(self, record: MessageAttachmentRecord) -> None:
        await delete_attachment_objects(
            session_factory=self._session_factory,
            object_store=self._object_store,
            record=record,
        )

    async def _validate_existing_turn(
        self,
        *,
        session: AsyncSession,
        transcript: AgentTranscriptRecord,
        text: str,
        attachment_ids: Sequence[str],
    ) -> None:
        if transcript.text != text:
            raise AttachmentConflictError(_IDEMPOTENCY_CONFLICT)
        result = await session.exec(
            select(MessageAttachmentRecord)
            .where(
                col(MessageAttachmentRecord.transcript_id) == transcript.id,
                col(MessageAttachmentRecord.user_id) == transcript.user_id,
            )
            .order_by(col(MessageAttachmentRecord.position)),
        )
        existing_ids = tuple(record.id for record in result.all())
        if existing_ids != tuple(attachment_ids):
            raise AttachmentConflictError(_IDEMPOTENCY_CONFLICT)

    def _validate_claim_records(
        self,
        *,
        records: Sequence[MessageAttachmentRecord],
        attachment_ids: Sequence[str],
    ) -> tuple[MessageAttachmentRecord, ...]:
        if len(attachment_ids) > self.settings.max_images_per_turn:
            message = f"A message can include at most {self.settings.max_images_per_turn} images"
            raise AttachmentValidationError(message)
        if len(set(attachment_ids)) != len(attachment_ids):
            raise AttachmentValidationError(_ATTACHMENT_SET_DUPLICATE)
        if len(records) != len(attachment_ids):
            raise AttachmentNotFoundError(_ATTACHMENT_SET_NOT_FOUND)
        by_id = {record.id: record for record in records}
        ordered = tuple(by_id[attachment_id] for attachment_id in attachment_ids)
        if any(record.state != AttachmentState.READY.value for record in ordered):
            raise AttachmentConflictError(_ATTACHMENT_SET_NOT_READY)
        if sum(record.byte_size for record in ordered) > self.settings.max_turn_bytes:
            raise AttachmentValidationError(_ATTACHMENT_SET_TOO_LARGE)
        return ordered

    async def _records_by_ids(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        chat_id: str,
        attachment_ids: Sequence[str],
        for_update: bool = False,
    ) -> tuple[MessageAttachmentRecord, ...]:
        if not attachment_ids:
            return ()
        statement = (
            select(MessageAttachmentRecord)
            .where(
                col(MessageAttachmentRecord.id).in_(attachment_ids),
                col(MessageAttachmentRecord.user_id) == user_id,
                col(MessageAttachmentRecord.chat_id) == chat_id,
            )
            .order_by(col(MessageAttachmentRecord.id))
        )
        if for_update:
            statement = statement.with_for_update()
        result = await session.exec(statement)
        return tuple(result.all())

    async def _existing_turn_record(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        chat_id: str,
        client_turn_id: str,
    ) -> AgentTranscriptRecord | None:
        result = await session.exec(
            select(AgentTranscriptRecord).where(
                col(AgentTranscriptRecord.user_id) == user_id,
                col(AgentTranscriptRecord.session_id) == chat_id,
                col(AgentTranscriptRecord.client_turn_id) == client_turn_id,
            ),
        )
        return result.first()

    async def _get_record(
        self,
        *,
        session: AsyncSession,
        attachment_id: str,
        user_id: str,
        chat_id: str,
        for_update: bool = False,
        read_lock: bool = False,
    ) -> MessageAttachmentRecord | None:
        statement = select(MessageAttachmentRecord).where(
            col(MessageAttachmentRecord.id) == attachment_id,
            col(MessageAttachmentRecord.user_id) == user_id,
            col(MessageAttachmentRecord.chat_id) == chat_id,
        )
        if for_update or read_lock:
            statement = statement.with_for_update(read=read_lock)
        result = await session.exec(statement)
        return result.first()

    async def _active_chat(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        chat_id: str,
        read_lock: bool = False,
    ) -> AgentSessionRecord | None:
        statement = select(AgentSessionRecord).where(
            col(AgentSessionRecord.id) == chat_id,
            col(AgentSessionRecord.user_id) == user_id,
            col(AgentSessionRecord.channel) == "text",
            col(AgentSessionRecord.deleted_at).is_(None),
        )
        if read_lock:
            statement = statement.with_for_update(read=True)
        result = await session.exec(statement)
        return result.first()


class AttachmentDeletionWorker:
    def __init__(
        self,
        *,
        session_factory: AsyncSessionFactory,
        object_store: AttachmentObjectStore,
        settings: AttachmentSettings,
    ) -> None:
        self._session_factory = session_factory
        self._object_store = object_store
        self._settings = settings
        self._next_run_at = 0.0

    async def run_if_due(self) -> int:
        now = asyncio.get_running_loop().time()
        if now < self._next_run_at:
            return 0
        self._next_run_at = now + self._settings.deletion_retry_interval_seconds
        return await self.run_once()

    async def run_once(self) -> int:
        async with self._session_factory() as session:
            result = await session.exec(
                select(MessageAttachmentRecord)
                .where(
                    col(MessageAttachmentRecord.state) == AttachmentState.DELETED.value,
                    col(MessageAttachmentRecord.storage_deleted_at).is_(None),
                    col(MessageAttachmentRecord.deletion_attempts) < self._settings.max_deletion_attempts,
                )
                .order_by(col(MessageAttachmentRecord.deleted_at))
                .limit(100),
            )
            records = tuple(result.all())
        for record in records:
            await delete_attachment_objects(
                session_factory=self._session_factory,
                object_store=self._object_store,
                record=record,
            )
        return len(records)


async def delete_attachment_objects(
    *,
    session_factory: AsyncSessionFactory,
    object_store: AttachmentObjectStore,
    record: MessageAttachmentRecord,
) -> None:
    storage_started = time.perf_counter()
    deletion_error: Exception | None = None
    try:
        await object_store.delete(record.object_key)
        await object_store.delete(record.thumbnail_key)
    except Exception as exc:
        deletion_error = exc
        logger.exception(
            "Failed to delete chat attachment objects",
            extra={"attachment_id": record.id},
        )
    finally:
        record_storage_latency(
            operation="Delete",
            duration_ms=(time.perf_counter() - storage_started) * 1000,
        )

    async with session_factory() as session:
        statement = (
            select(MessageAttachmentRecord)
            .where(
                col(MessageAttachmentRecord.id) == record.id,
                col(MessageAttachmentRecord.user_id) == record.user_id,
                col(MessageAttachmentRecord.chat_id) == record.chat_id,
            )
            .with_for_update()
        )
        stored = (await session.exec(statement)).first()
        if stored is None or stored.storage_deleted_at is not None:
            return
        now = utc_now()
        if deletion_error is None:
            stored.storage_deleted_at = now
            stored.deletion_error = None
        else:
            stored.deletion_attempts += 1
            stored.deletion_error = type(deletion_error).__name__[:256]
        stored.updated_at = now
        session.add(stored)
        await session.commit()


def normalize_image(content: bytes, settings: AttachmentSettings) -> NormalizedImage:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as opened:
                image_format = (opened.format or "").upper()
                if image_format not in _SUPPORTED_FORMATS:
                    raise AttachmentValidationError(
                        _IMAGE_FORMAT_INVALID,
                        code="unsupported_format",
                    )
                if getattr(opened, "n_frames", 1) != 1:
                    raise AttachmentValidationError(
                        _IMAGE_ANIMATED,
                        code="animated_image",
                    )
                if opened.width * opened.height > settings.max_pixels:
                    raise AttachmentValidationError(
                        _IMAGE_PIXELS_EXCEEDED,
                        code="pixel_limit_exceeded",
                    )
                opened.load()
                image = ImageOps.exif_transpose(opened)
                image.thumbnail(
                    (settings.max_dimension, settings.max_dimension),
                    Image.Resampling.LANCZOS,
                )
                normalized, mime_type = encode_normalized(image)
                thumbnail_image = image.copy()
                thumbnail_image.thumbnail(
                    (settings.thumbnail_dimension, settings.thumbnail_dimension),
                    Image.Resampling.LANCZOS,
                )
                thumbnail, _ = encode_normalized(
                    thumbnail_image,
                    mime_type=mime_type,
                )
    except AttachmentValidationError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as exc:
        raise AttachmentValidationError(
            _IMAGE_DECODE_FAILED,
            code="decode_failed",
        ) from exc

    if len(normalized) > settings.max_image_bytes:
        raise AttachmentValidationError(
            _NORMALIZED_IMAGE_TOO_LARGE,
            code="normalized_bytes_exceeded",
        )
    digest = sha256(normalized).hexdigest()
    return NormalizedImage(
        content=normalized,
        thumbnail=thumbnail,
        mime_type=mime_type,
        byte_size=len(normalized),
        width=image.width,
        height=image.height,
        sha256=digest,
    )


def encode_normalized(
    image: Image.Image,
    *,
    mime_type: ImageMimeType | None = None,
) -> tuple[bytes, ImageMimeType]:
    has_alpha = image.mode in {"LA", "PA", "RGBA"} or (image.mode == "P" and "transparency" in image.info)
    resolved_mime = mime_type or ("image/png" if has_alpha else "image/jpeg")
    output = BytesIO()
    if resolved_mime == "image/png":
        converted = image.convert("RGBA" if has_alpha else "RGB")
        converted.save(output, format="PNG", optimize=True)
        return output.getvalue(), "image/png"
    converted = image.convert("RGB")
    converted.save(
        output,
        format="JPEG",
        quality=_JPEG_QUALITY,
        optimize=True,
        progressive=True,
    )
    return output.getvalue(), "image/jpeg"


def safe_display_name(filename: str | None) -> str:
    if filename is None:
        return "image"
    name = PurePath(filename).name.strip()
    normalized = _SAFE_NAME.sub("_", name)[:128].strip(" .")
    return normalized or "image"


def attachment_summary(record: MessageAttachmentRecord) -> AttachmentSummary:
    return AttachmentSummary(
        id=record.id,
        mime_type=cast(ImageMimeType, record.mime_type),
        byte_size=record.byte_size,
        width=record.width,
        height=record.height,
        position=record.position,
    )


def _generated_output_client_turn_id(client_turn_id: str) -> str:
    return f"image:{client_turn_id}"

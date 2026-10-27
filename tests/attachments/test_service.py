from datetime import timedelta
from io import BytesIO

import pytest
from PIL import Image
from sqlmodel import col, select

from voxos.agent_runtime.records import AgentSessionRecord, AgentTranscriptRecord
from voxos.attachments.models import (
    AttachmentClientSource,
    AttachmentState,
    AttachmentVariant,
)
from voxos.attachments.records import MessageAttachmentRecord
from voxos.attachments.service import (
    AttachmentDeletionWorker,
    AttachmentNotFoundError,
    AttachmentService,
    AttachmentValidationError,
    normalize_image,
)
from voxos.attachments.settings import AttachmentSettings
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory


class MemoryAttachmentStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def get(self, key: str) -> bytes:
        try:
            return self.objects[key]
        except KeyError as exc:
            raise FileNotFoundError(key) from exc

    async def put(self, key: str, content: bytes, content_type: str) -> None:
        del content_type
        self.objects[key] = content


class RetryableDeleteStore(MemoryAttachmentStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail_deletes = False

    async def delete(self, key: str) -> None:
        if self.fail_deletes:
            message = "object store unavailable"
            raise OSError(message)
        await super().delete(key)


class FailedUploadStore(RetryableDeleteStore):
    async def put(self, key: str, content: bytes, content_type: str) -> None:
        del key, content, content_type
        message = "object store upload unavailable"
        raise OSError(message)


class BytesUpload:
    def __init__(self, content: bytes, filename: str = "photo.jpg") -> None:
        self.filename = filename
        self._content = content
        self._position = 0

    async def read(self, size: int = -1) -> bytes:
        if self._position >= len(self._content):
            return b""
        end = len(self._content) if size < 0 else self._position + size
        chunk = self._content[self._position : end]
        self._position += len(chunk)
        return chunk


@pytest.mark.asyncio
async def test_upload_claim_read_and_idempotent_retry_are_one_ordered_turn(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    await add_chat(sqlite_session_factory, chat_id="chat-1", user_id="user-1")
    store = MemoryAttachmentStore()
    service = AttachmentService(
        session_factory=sqlite_session_factory,
        object_store=store,
        settings=AttachmentSettings(),
    )
    first = await service.upload(
        user_id="user-1",
        chat_id="chat-1",
        client_draft_id="draft-1",
        client_source=AttachmentClientSource.WEB,
        upload=BytesUpload(image_bytes("JPEG", color="red"), "one.jpg"),
    )
    second = await service.upload(
        user_id="user-1",
        chat_id="chat-1",
        client_draft_id="draft-1",
        client_source=AttachmentClientSource.WEB,
        upload=BytesUpload(image_bytes("PNG", color="blue"), "two.png"),
    )

    claimed = await service.claim_turn(
        user_id="user-1",
        chat_id="chat-1",
        agent_id="agent-1",
        text="Compare these",
        attachment_ids=(second.id, first.id),
        client_turn_id="turn-1",
    )
    retry = await service.claim_turn(
        user_id="user-1",
        chat_id="chat-1",
        agent_id="agent-1",
        text="Compare these",
        attachment_ids=(second.id, first.id),
        client_turn_id="turn-1",
    )

    assert claimed.created is True
    assert retry.created is False
    assert retry.transcript_id == claimed.transcript_id
    summaries = await service.summaries_for_transcripts(
        user_id="user-1",
        chat_id="chat-1",
        transcript_ids=(claimed.transcript_id,),
    )
    assert [item.id for item in summaries[claimed.transcript_id]] == [
        second.id,
        first.id,
    ]
    assert [item.position for item in summaries[claimed.transcript_id]] == [0, 1]
    content, mime_type = await service.get_content(
        user_id="user-1",
        chat_id="chat-1",
        attachment_id=first.id,
        variant=AttachmentVariant.FULL,
    )
    assert mime_type == "image/jpeg"
    assert Image.open(BytesIO(content)).getexif() == {}

    async with sqlite_session_factory() as session:
        transcripts = (
            await session.exec(
                select(AgentTranscriptRecord).where(
                    col(AgentTranscriptRecord.client_turn_id) == "turn-1",
                ),
            )
        ).all()
    assert len(transcripts) == 1


@pytest.mark.asyncio
async def test_attachment_access_is_tenant_and_chat_scoped(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    await add_chat(sqlite_session_factory, chat_id="chat-1", user_id="user-1")
    await add_chat(sqlite_session_factory, chat_id="chat-2", user_id="user-2")
    service = AttachmentService(
        session_factory=sqlite_session_factory,
        object_store=MemoryAttachmentStore(),
        settings=AttachmentSettings(),
    )
    record = await service.upload(
        user_id="user-1",
        chat_id="chat-1",
        client_draft_id="draft-1",
        client_source=AttachmentClientSource.FLUTTER,
        upload=BytesUpload(image_bytes("PNG")),
    )

    with pytest.raises(AttachmentNotFoundError):
        await service.get_content(
            user_id="user-2",
            chat_id="chat-2",
            attachment_id=record.id,
            variant=AttachmentVariant.THUMBNAIL,
        )
    with pytest.raises(AttachmentNotFoundError):
        await service.delete_staged(
            user_id="user-2",
            chat_id="chat-2",
            attachment_id=record.id,
        )
    with pytest.raises(AttachmentNotFoundError):
        await service.claim_turn(
            user_id="user-2",
            chat_id="chat-2",
            agent_id="agent-2",
            text="steal",
            attachment_ids=(record.id,),
            client_turn_id="turn-2",
        )


@pytest.mark.asyncio
async def test_invalid_and_oversized_uploads_leave_no_ready_attachment(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    await add_chat(sqlite_session_factory, chat_id="chat-1", user_id="user-1")
    service = AttachmentService(
        session_factory=sqlite_session_factory,
        object_store=MemoryAttachmentStore(),
        settings=AttachmentSettings(max_source_bytes=1024),
    )

    with pytest.raises(AttachmentValidationError):
        await service.upload(
            user_id="user-1",
            chat_id="chat-1",
            client_draft_id="draft-1",
            client_source=AttachmentClientSource.WEB,
            upload=BytesUpload(b"x" * 1025),
        )
    with pytest.raises(AttachmentValidationError):
        await service.upload(
            user_id="user-1",
            chat_id="chat-1",
            client_draft_id="draft-1",
            client_source=AttachmentClientSource.WEB,
            upload=BytesUpload(b"not an image"),
        )
    async with sqlite_session_factory() as session:
        records = (await session.exec(select(MessageAttachmentRecord))).all()
    assert records == []


@pytest.mark.asyncio
async def test_deleted_attachment_is_hidden_and_physical_delete_can_retry(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    await add_chat(sqlite_session_factory, chat_id="chat-1", user_id="user-1")
    store = RetryableDeleteStore()
    service = AttachmentService(
        session_factory=sqlite_session_factory,
        object_store=store,
        settings=AttachmentSettings(max_deletion_attempts=2),
    )
    record = await service.upload(
        user_id="user-1",
        chat_id="chat-1",
        client_draft_id="draft-1",
        client_source=AttachmentClientSource.WEB,
        upload=BytesUpload(image_bytes("JPEG")),
    )
    store.fail_deletes = True

    await service.delete_staged(
        user_id="user-1",
        chat_id="chat-1",
        attachment_id=record.id,
    )

    with pytest.raises(AttachmentNotFoundError):
        await service.get_content(
            user_id="user-1",
            chat_id="chat-1",
            attachment_id=record.id,
            variant=AttachmentVariant.FULL,
        )
    async with sqlite_session_factory() as session:
        failed = await session.get(MessageAttachmentRecord, record.id)
    assert failed is not None
    assert failed.deletion_attempts == 1
    assert failed.deletion_error == "OSError"

    store.fail_deletes = False
    assert await service.retry_pending_deletions(user_id="user-1") == 1
    assert store.objects == {}
    async with sqlite_session_factory() as session:
        retried = await session.get(MessageAttachmentRecord, record.id)
    assert retried is not None
    assert retried.deletion_attempts == 1
    assert retried.deletion_error is None
    assert retried.storage_deleted_at is not None


@pytest.mark.asyncio
async def test_stale_unclaimed_attachment_cleanup_is_idempotent(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    await add_chat(sqlite_session_factory, chat_id="chat-1", user_id="user-1")
    store = MemoryAttachmentStore()
    service = AttachmentService(
        session_factory=sqlite_session_factory,
        object_store=store,
        settings=AttachmentSettings(staging_ttl_seconds=300),
    )
    record = await service.upload(
        user_id="user-1",
        chat_id="chat-1",
        client_draft_id="draft-1",
        client_source=AttachmentClientSource.FLUTTER,
        upload=BytesUpload(image_bytes("PNG")),
    )
    async with sqlite_session_factory() as session:
        stored = await session.get(MessageAttachmentRecord, record.id)
        assert stored is not None
        stored.created_at = utc_now() - timedelta(seconds=301)
        session.add(stored)
        await session.commit()

    assert await service.cleanup_stale(user_id="user-1") == 1
    assert await service.cleanup_stale(user_id="user-1") == 0
    assert store.objects == {}


@pytest.mark.asyncio
async def test_content_is_hidden_as_soon_as_parent_chat_is_deleted(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    await add_chat(sqlite_session_factory, chat_id="chat-1", user_id="user-1")
    store = MemoryAttachmentStore()
    service = AttachmentService(
        session_factory=sqlite_session_factory,
        object_store=store,
        settings=AttachmentSettings(),
    )
    record = await service.upload(
        user_id="user-1",
        chat_id="chat-1",
        client_draft_id="draft-1",
        client_source=AttachmentClientSource.WEB,
        upload=BytesUpload(image_bytes("JPEG")),
    )
    async with sqlite_session_factory() as session:
        chat = await session.get(AgentSessionRecord, "chat-1")
        assert chat is not None
        chat.deleted_at = utc_now()
        session.add(chat)
        await session.commit()

    with pytest.raises(AttachmentNotFoundError):
        await service.get_content(
            user_id="user-1",
            chat_id="chat-1",
            attachment_id=record.id,
            variant=AttachmentVariant.FULL,
        )
    assert store.objects


@pytest.mark.asyncio
async def test_retry_finds_logical_delete_without_a_recorded_error(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    await add_chat(sqlite_session_factory, chat_id="chat-1", user_id="user-1")
    store = MemoryAttachmentStore()
    service = AttachmentService(
        session_factory=sqlite_session_factory,
        object_store=store,
        settings=AttachmentSettings(),
    )
    record = await service.upload(
        user_id="user-1",
        chat_id="chat-1",
        client_draft_id="draft-1",
        client_source=AttachmentClientSource.WEB,
        upload=BytesUpload(image_bytes("PNG")),
    )
    async with sqlite_session_factory() as session:
        stored = await session.get(MessageAttachmentRecord, record.id)
        assert stored is not None
        stored.state = AttachmentState.DELETED.value
        stored.deleted_at = utc_now()
        stored.deletion_error = None
        stored.storage_deleted_at = None
        session.add(stored)
        await session.commit()

    assert await service.retry_pending_deletions(user_id="user-1") == 1
    assert store.objects == {}
    async with sqlite_session_factory() as session:
        retried = await session.get(MessageAttachmentRecord, record.id)
    assert retried is not None
    assert retried.deletion_error is None
    assert retried.storage_deleted_at is not None


@pytest.mark.asyncio
async def test_failed_upload_enters_durable_deletion_queue(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    await add_chat(sqlite_session_factory, chat_id="chat-1", user_id="user-1")
    store = FailedUploadStore()
    store.fail_deletes = True
    service = AttachmentService(
        session_factory=sqlite_session_factory,
        object_store=store,
        settings=AttachmentSettings(),
    )

    with pytest.raises(OSError, match="upload unavailable"):
        await service.upload(
            user_id="user-1",
            chat_id="chat-1",
            client_draft_id="draft-1",
            client_source=AttachmentClientSource.WEB,
            upload=BytesUpload(image_bytes("PNG")),
        )

    async with sqlite_session_factory() as session:
        failed = (await session.exec(select(MessageAttachmentRecord))).one()
    assert failed.state == AttachmentState.DELETED.value
    assert failed.storage_deleted_at is None
    assert failed.deletion_error == "OSError"

    store.fail_deletes = False
    assert await service.retry_pending_deletions(user_id="user-1") == 1
    async with sqlite_session_factory() as session:
        retried = await session.get(MessageAttachmentRecord, failed.id)
    assert retried is not None
    assert retried.storage_deleted_at is not None
    assert retried.deletion_error is None


@pytest.mark.asyncio
async def test_deletion_worker_processes_pending_rows_across_users(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    store = MemoryAttachmentStore()
    service = AttachmentService(
        session_factory=sqlite_session_factory,
        object_store=store,
        settings=AttachmentSettings(),
    )
    records: list[MessageAttachmentRecord] = []
    for index in (1, 2):
        user_id = f"user-{index}"
        chat_id = f"chat-{index}"
        await add_chat(
            sqlite_session_factory,
            chat_id=chat_id,
            user_id=user_id,
        )
        record = await service.upload(
            user_id=user_id,
            chat_id=chat_id,
            client_draft_id=f"draft-{index}",
            client_source=AttachmentClientSource.WEB,
            upload=BytesUpload(image_bytes("PNG")),
        )
        records.append(record)
    async with sqlite_session_factory() as session:
        for record in records:
            stored = await session.get(MessageAttachmentRecord, record.id)
            assert stored is not None
            stored.state = AttachmentState.DELETED.value
            stored.deleted_at = utc_now()
            stored.storage_deleted_at = None
            session.add(stored)
        await session.commit()

    worker = AttachmentDeletionWorker(
        session_factory=sqlite_session_factory,
        object_store=store,
        settings=AttachmentSettings(),
    )
    assert await worker.run_once() == 2
    assert store.objects == {}
    async with sqlite_session_factory() as session:
        deleted_records = tuple(
            (
                await session.exec(
                    select(MessageAttachmentRecord).where(
                        col(MessageAttachmentRecord.id).in_(
                            tuple(record.id for record in records),
                        ),
                    ),
                )
            ).all(),
        )
    assert all(record.storage_deleted_at is not None for record in deleted_records)


@pytest.mark.asyncio
async def test_generated_image_is_persisted_as_idempotent_assistant_attachment(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    await add_chat(sqlite_session_factory, chat_id="chat-1", user_id="user-1")
    store = MemoryAttachmentStore()
    service = AttachmentService(
        session_factory=sqlite_session_factory,
        object_store=store,
        settings=AttachmentSettings(),
    )
    source = await service.upload(
        user_id="user-1",
        chat_id="chat-1",
        client_draft_id="draft-1",
        client_source=AttachmentClientSource.FLUTTER,
        upload=BytesUpload(image_bytes("PNG", color="blue")),
    )

    first = await service.persist_generated_turn(
        user_id="user-1",
        chat_id="chat-1",
        agent_id="agent-user-1",
        prompt="Turn this into a watercolor",
        source_attachment_ids=(source.id,),
        client_turn_id="turn-1",
        client_source=AttachmentClientSource.FLUTTER,
        generated_content=image_bytes("PNG", color="purple"),
        assistant_text="Generated with GPT Image 2 via OpenAI.",
    )
    retry = await service.persist_generated_turn(
        user_id="user-1",
        chat_id="chat-1",
        agent_id="agent-user-1",
        prompt="Turn this into a watercolor",
        source_attachment_ids=(source.id,),
        client_turn_id="turn-1",
        client_source=AttachmentClientSource.FLUTTER,
        generated_content=image_bytes("PNG", color="red"),
        assistant_text="Generated with GPT Image 2 via OpenAI.",
    )

    user_message, assistant_message, output = first
    assert retry[2].id == output.id
    assert user_message.role == "user"
    assert assistant_message.role == "assistant"
    summaries = await service.summaries_for_transcripts(
        user_id="user-1",
        chat_id="chat-1",
        transcript_ids=(user_message.id, assistant_message.id),
    )
    assert summaries[user_message.id][0].id == source.id
    assert summaries[assistant_message.id][0].id == output.id
    content, mime_type = await service.get_content(
        user_id="user-1",
        chat_id="chat-1",
        attachment_id=output.id,
        variant=AttachmentVariant.FULL,
    )
    assert mime_type == "image/jpeg"
    assert Image.open(BytesIO(content)).getpixel((0, 0))[2] > 80


def test_pixel_limit_is_checked_before_image_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = Image.new("RGB", (1001, 1000), color="green")
    buffer = BytesIO()
    image.save(buffer, format="PNG")

    def fail_load(_image: Image.Image) -> None:
        message = "image pixels were decoded"
        raise AssertionError(message)

    monkeypatch.setattr(Image.Image, "load", fail_load)

    with pytest.raises(AttachmentValidationError) as exc_info:
        normalize_image(
            buffer.getvalue(),
            AttachmentSettings(max_pixels=1_000_000),
        )

    assert exc_info.value.code == "pixel_limit_exceeded"


async def add_chat(
    session_factory: AsyncSessionFactory,
    *,
    chat_id: str,
    user_id: str,
) -> None:
    async with session_factory() as session:
        session.add(
            AgentSessionRecord(
                id=chat_id,
                user_id=user_id,
                agent_id=f"agent-{user_id}",
                provider="openai",
                model="gpt-5.6-sol",
                channel="text",
            ),
        )
        await session.commit()


def image_bytes(format_name: str, *, color: str = "green") -> bytes:
    image = Image.new("RGB", (64, 48), color=color)
    buffer = BytesIO()
    image.save(buffer, format=format_name)
    return buffer.getvalue()

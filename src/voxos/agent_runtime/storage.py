from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar

from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_runtime.history import ACCOUNT_LEARNING_TOOLS
from voxos.agent_runtime.improvements import withdraw_session_learning
from voxos.agent_runtime.learning_lock import lock_learning_settings
from voxos.agent_runtime.learning_triggers import enqueue_error_reflection
from voxos.agent_runtime.models import (
    AgentSessionStatus,
    AgentStateEvent,
    AgentToolEvent,
    AgentTranscriptItem,
    AgentUsageSnapshot,
    JsonObject,
)
from voxos.agent_runtime.records import (
    AgentRuntimeEventRecord,
    AgentScheduleRecord,
    AgentSessionRecord,
    AgentToolEventRecord,
    AgentTranscriptRecord,
    AgentUsageRecord,
)
from voxos.agent_runtime.schedule_models import ScheduleStatus
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.notifications.outbox import enqueue_chat_message_notification


class SessionScopedRecord(Protocol):
    session_id: str
    agent_id: str | None


SessionRecordT = TypeVar("SessionRecordT", bound=SessionScopedRecord)


@dataclass(frozen=True, slots=True)
class TranscriptAppendResult:
    record: AgentTranscriptRecord
    created: bool


def recent_records_per_session(
    records: Sequence[SessionRecordT],
    limit_per_session: int,
) -> tuple[SessionRecordT, ...]:
    if limit_per_session < 1:
        message = "limit_per_session must be positive"
        raise ValueError(message)
    counts: dict[tuple[str, str | None], int] = {}
    recent_records: list[SessionRecordT] = []
    for record in records:
        key = (record.session_id, record.agent_id)
        count = counts.get(key, 0)
        if count >= limit_per_session:
            continue
        counts[key] = count + 1
        recent_records.append(record)
    return tuple(reversed(recent_records))


def validate_existing_transcript(
    *,
    existing: AgentTranscriptRecord,
    item: AgentTranscriptItem,
) -> None:
    if (
        existing.user_id != item.user_id
        or existing.session_id != item.session_id
        or existing.agent_id != item.agent_id
        or existing.role != item.role.value
        or existing.text != item.text
        or existing.source != item.source.value
    ):
        message = "Client turn ID is already used by a different chat message"
        raise ValueError(message)


class AgentRuntimeStore(Protocol):
    async def create_session(
        self,
        *,
        session_id: str | None = None,
        user_id: str,
        agent_id: str,
        provider: str,
        model: str,
        channel: str = "livekit",
        owner_authenticated: bool = False,
        title: str | None = None,
        voice: str | None,
        room_name: str | None,
        room_metadata: JsonObject,
        prompt_version: str | None,
        prompt_snapshot: str | None,
        prompt_hash: str | None,
        effective_tool_policy_json: JsonObject | None = None,
    ) -> AgentSessionRecord:
        """Create a durable runtime session record."""

    async def end_session(
        self,
        *,
        session_id: str,
        user_id: str,
        agent_id: str,
        close_reason: str | None,
        close_error_type: str | None,
        close_error_message: str | None,
    ) -> AgentSessionRecord | None:
        """Mark a runtime session as ended."""

    async def fail_session(
        self,
        *,
        session_id: str,
        user_id: str,
        agent_id: str,
        error_type: str,
        error_message: str | None,
    ) -> AgentSessionRecord | None:
        """Mark a runtime session as failed."""

    async def append_transcript_item(
        self,
        item: AgentTranscriptItem,
    ) -> AgentTranscriptRecord:
        """Persist a transcript item."""

    async def append_chat_message_once(
        self,
        item: AgentTranscriptItem,
    ) -> TranscriptAppendResult | None:
        """Persist one idempotent assistant message to an active text chat."""

    async def append_tool_event(self, event: AgentToolEvent) -> AgentToolEventRecord:
        """Persist a tool execution event."""

    async def append_usage_snapshot(
        self,
        snapshot: AgentUsageSnapshot,
    ) -> AgentUsageRecord:
        """Persist a usage snapshot."""

    async def append_runtime_event(
        self,
        event: AgentStateEvent,
    ) -> AgentRuntimeEventRecord:
        """Persist a low-level runtime event."""

    async def list_transcript_items(
        self,
        *,
        session_id: str,
        user_id: str,
        agent_id: str,
        limit: int | None = None,
    ) -> tuple[AgentTranscriptRecord, ...]:
        """Return retained transcript items for a session and user."""

    async def list_transcript_items_for_sessions(
        self,
        *,
        session_ids: Sequence[str],
        user_id: str,
        limit_per_session: int,
    ) -> tuple[AgentTranscriptRecord, ...]:
        """Return recent transcript items for several user-owned sessions."""

    async def list_tool_events_for_sessions(
        self,
        *,
        session_ids: Sequence[str],
        user_id: str,
        limit_per_session: int,
    ) -> tuple[AgentToolEventRecord, ...]:
        """Return recent tool events for several user-owned sessions."""

    async def list_runtime_events_for_sessions(
        self,
        *,
        session_ids: Sequence[str],
        user_id: str,
        limit_per_session: int,
    ) -> tuple[AgentRuntimeEventRecord, ...]:
        """Return recent runtime events for several user-owned sessions."""


class SqlModelAgentRuntimeStore:
    def __init__(
        self,
        session_factory: AsyncSessionFactory,
        *,
        chat_notifications_enabled: bool = False,
        chat_notification_max_attempts: int = 5,
    ) -> None:
        self._session_factory = session_factory
        self._chat_notifications_enabled = chat_notifications_enabled
        self._chat_notification_max_attempts = chat_notification_max_attempts

    async def create_session(
        self,
        *,
        session_id: str | None = None,
        user_id: str,
        agent_id: str,
        provider: str,
        model: str,
        channel: str = "livekit",
        owner_authenticated: bool = False,
        title: str | None = None,
        voice: str | None,
        room_name: str | None,
        room_metadata: JsonObject,
        prompt_version: str | None,
        prompt_snapshot: str | None,
        prompt_hash: str | None,
        effective_tool_policy_json: JsonObject | None = None,
    ) -> AgentSessionRecord:
        record = AgentSessionRecord(
            user_id=user_id,
            agent_id=agent_id,
            provider=provider,
            model=model,
            channel=channel,
            owner_authenticated=owner_authenticated,
            title=title,
            voice=voice,
            room_name=room_name,
            room_metadata=room_metadata,
            effective_tool_policy_json=effective_tool_policy_json or {},
            prompt_version=prompt_version,
            prompt_snapshot=prompt_snapshot,
            prompt_hash=prompt_hash,
        )
        if session_id is not None:
            record.id = session_id
        async with self._session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def get_session(
        self,
        *,
        session_id: str,
        user_id: str,
        agent_id: str | None = None,
        channel: str | None = None,
        include_deleted: bool = False,
    ) -> AgentSessionRecord | None:
        statement = select(AgentSessionRecord).where(
            col(AgentSessionRecord.id) == session_id,
            col(AgentSessionRecord.user_id) == user_id,
        )
        if agent_id is not None:
            statement = statement.where(col(AgentSessionRecord.agent_id) == agent_id)
        if channel is not None:
            statement = statement.where(col(AgentSessionRecord.channel) == channel)
        if not include_deleted:
            statement = statement.where(col(AgentSessionRecord.deleted_at).is_(None))
        async with self._session_factory() as session:
            result = await session.exec(statement)
            return result.one_or_none()

    async def list_sessions(
        self,
        *,
        user_id: str,
        agent_id: str | None = None,
        channel: str | None = None,
        limit: int = 50,
    ) -> tuple[AgentSessionRecord, ...]:
        statement = select(AgentSessionRecord).where(
            col(AgentSessionRecord.user_id) == user_id,
            col(AgentSessionRecord.deleted_at).is_(None),
        )
        if agent_id is not None:
            statement = statement.where(col(AgentSessionRecord.agent_id) == agent_id)
        if channel is not None:
            statement = statement.where(col(AgentSessionRecord.channel) == channel)
        order_column = AgentSessionRecord.updated_at if channel == "text" else AgentSessionRecord.started_at
        statement = statement.order_by(col(order_column).desc()).limit(limit)
        async with self._session_factory() as session:
            result = await session.exec(statement)
            return tuple(result.all())

    async def update_session_title(
        self,
        *,
        session_id: str,
        user_id: str,
        agent_id: str,
        title: str,
    ) -> AgentSessionRecord | None:
        async with self._session_factory() as session:
            record = await session.get(AgentSessionRecord, session_id)
            if (
                record is None
                or record.user_id != user_id
                or record.agent_id != agent_id
                or record.deleted_at is not None
            ):
                return None
            record.title = title
            record.updated_at = utc_now()
            await session.commit()
            return record

    async def update_session_chat_settings(
        self,
        *,
        session_id: str,
        user_id: str,
        agent_id: str,
        title: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> AgentSessionRecord | None:
        async with self._session_factory() as session:
            record = await session.get(AgentSessionRecord, session_id)
            if (
                record is None
                or record.user_id != user_id
                or record.agent_id != agent_id
                or record.channel != "text"
                or record.deleted_at is not None
            ):
                return None
            if title is not None:
                record.title = title
            if provider is not None:
                record.provider = provider
            if model is not None:
                record.model = model
            record.updated_at = utc_now()
            await session.commit()
            await session.refresh(record)
            return record

    async def soft_delete_session(
        self,
        *,
        session_id: str,
        user_id: str,
        agent_id: str,
    ) -> AgentSessionRecord | None:
        now = utc_now()
        async with self._session_factory() as session:
            await lock_learning_settings(session, user_id)
            record = await session.get(AgentSessionRecord, session_id)
            if (
                record is None
                or record.user_id != user_id
                or record.agent_id != agent_id
                or record.deleted_at is not None
            ):
                return None
            await withdraw_session_learning(session, user_id=user_id, session_id=session_id)
            record.deleted_at = now
            record.ended_at = record.ended_at or now
            record.updated_at = now
            record.status = AgentSessionStatus.ENDED.value
            record.title = "Deleted chat"
            record.close_reason = "deleted"
            record.close_error_type = None
            record.close_error_message = None

            transcripts = await session.exec(
                select(AgentTranscriptRecord).where(
                    col(AgentTranscriptRecord.session_id) == session_id,
                    col(AgentTranscriptRecord.user_id) == user_id,
                    col(AgentTranscriptRecord.agent_id) == agent_id,
                ),
            )
            for transcript in transcripts.all():
                transcript.text = "[deleted]"

            tool_events = await session.exec(
                select(AgentToolEventRecord).where(
                    col(AgentToolEventRecord.session_id) == session_id,
                    col(AgentToolEventRecord.user_id) == user_id,
                    col(AgentToolEventRecord.agent_id) == agent_id,
                ),
            )
            for tool_event in tool_events.all():
                tool_event.arguments_json = {}
                tool_event.output_text = None

            usage_events = await session.exec(
                select(AgentUsageRecord).where(
                    col(AgentUsageRecord.session_id) == session_id,
                    col(AgentUsageRecord.user_id) == user_id,
                    col(AgentUsageRecord.agent_id) == agent_id,
                ),
            )
            for usage_event in usage_events.all():
                usage_event.raw_usage_json = {}

            schedules = await session.exec(
                select(AgentScheduleRecord).where(
                    col(AgentScheduleRecord.user_id) == user_id,
                    col(AgentScheduleRecord.agent_id) == agent_id,
                    col(AgentScheduleRecord.chat_id) == session_id,
                    col(AgentScheduleRecord.deleted_at).is_(None),
                    col(AgentScheduleRecord.enabled).is_(True),
                ),
            )
            for schedule in schedules.all():
                schedule.enabled = False
                schedule.status = ScheduleStatus.PAUSED.value
                schedule.updated_at = now
                session.add(schedule)

            runtime_events = await session.exec(
                select(AgentRuntimeEventRecord).where(
                    col(AgentRuntimeEventRecord.session_id) == session_id,
                    col(AgentRuntimeEventRecord.user_id) == user_id,
                    col(AgentRuntimeEventRecord.agent_id) == agent_id,
                ),
            )
            for runtime_event in runtime_events.all():
                runtime_event.payload_json = {"redacted": True}

            await session.commit()
            await session.refresh(record)
            return record

    async def end_session(
        self,
        *,
        session_id: str,
        user_id: str,
        agent_id: str,
        close_reason: str | None,
        close_error_type: str | None,
        close_error_message: str | None,
    ) -> AgentSessionRecord | None:
        return await self._update_session_status(
            session_id=session_id,
            user_id=user_id,
            agent_id=agent_id,
            status=AgentSessionStatus.ENDED,
            close_reason=close_reason,
            close_error_type=close_error_type,
            close_error_message=close_error_message,
        )

    async def fail_session(
        self,
        *,
        session_id: str,
        user_id: str,
        agent_id: str,
        error_type: str,
        error_message: str | None,
    ) -> AgentSessionRecord | None:
        return await self._update_session_status(
            session_id=session_id,
            user_id=user_id,
            agent_id=agent_id,
            status=AgentSessionStatus.FAILED,
            close_reason="startup_failed",
            close_error_type=error_type,
            close_error_message=error_message,
        )

    async def append_transcript_item(
        self,
        item: AgentTranscriptItem,
    ) -> AgentTranscriptRecord:
        record = AgentTranscriptRecord(
            session_id=item.session_id,
            user_id=item.user_id,
            agent_id=item.agent_id,
            role=item.role.value,
            text=item.text,
            client_turn_id=item.client_turn_id,
            source=item.source.value,
            owner_authenticated=item.owner_authenticated,
            interrupted=item.interrupted,
            transcript_confidence=item.transcript_confidence,
            language=item.language,
            speaker_id=item.speaker_id,
            is_final=item.is_final,
            livekit_item_id=item.livekit_item_id,
            created_at=item.created_at,
        )
        async with self._session_factory() as session:
            session.add(record)
            chat = await session.get(AgentSessionRecord, item.session_id)
            if (
                chat is not None
                and chat.user_id == item.user_id
                and chat.agent_id == item.agent_id
                and chat.channel == "text"
                and chat.deleted_at is None
            ):
                chat.updated_at = item.created_at
                session.add(chat)
                if self._chat_notifications_enabled:
                    await enqueue_chat_message_notification(
                        session=session,
                        item=item,
                        transcript_id=record.id,
                        max_attempts=self._chat_notification_max_attempts,
                    )
            await session.commit()
        return record

    async def append_chat_message_once(
        self,
        item: AgentTranscriptItem,
    ) -> TranscriptAppendResult | None:
        if item.client_turn_id is None:
            message = "Idempotent chat messages require a client turn ID"
            raise ValueError(message)
        async with self._session_factory() as session:
            chat = await session.get(AgentSessionRecord, item.session_id)
            if (
                chat is None
                or chat.user_id != item.user_id
                or chat.agent_id != item.agent_id
                or chat.channel != "text"
                or chat.deleted_at is not None
            ):
                return None
            existing = await self._transcript_by_client_turn_id(
                session=session,
                item=item,
            )
            if existing is not None:
                validate_existing_transcript(existing=existing, item=item)
                return TranscriptAppendResult(record=existing, created=False)
            record = AgentTranscriptRecord(
                session_id=item.session_id,
                user_id=item.user_id,
                agent_id=item.agent_id,
                role=item.role.value,
                text=item.text,
                client_turn_id=item.client_turn_id,
                source=item.source.value,
                owner_authenticated=item.owner_authenticated,
                interrupted=item.interrupted,
                transcript_confidence=item.transcript_confidence,
                language=item.language,
                speaker_id=item.speaker_id,
                is_final=item.is_final,
                livekit_item_id=item.livekit_item_id,
                created_at=item.created_at,
            )
            session.add(record)
            chat.updated_at = item.created_at
            session.add(chat)
            if self._chat_notifications_enabled:
                await enqueue_chat_message_notification(
                    session=session,
                    item=item,
                    transcript_id=record.id,
                    max_attempts=self._chat_notification_max_attempts,
                )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await self._transcript_by_client_turn_id(
                    session=session,
                    item=item,
                )
                if existing is None:
                    raise
                validate_existing_transcript(existing=existing, item=item)
                return TranscriptAppendResult(record=existing, created=False)
            return TranscriptAppendResult(record=record, created=True)

    async def _transcript_by_client_turn_id(
        self,
        *,
        session: AsyncSession,
        item: AgentTranscriptItem,
    ) -> AgentTranscriptRecord | None:
        result = await session.exec(
            select(AgentTranscriptRecord).where(
                col(AgentTranscriptRecord.user_id) == item.user_id,
                col(AgentTranscriptRecord.session_id) == item.session_id,
                col(AgentTranscriptRecord.client_turn_id) == item.client_turn_id,
            ),
        )
        return result.one_or_none()

    async def append_tool_event(self, event: AgentToolEvent) -> AgentToolEventRecord:
        record = AgentToolEventRecord(
            session_id=event.session_id,
            user_id=event.user_id,
            agent_id=event.agent_id,
            call_id=event.call_id,
            name=event.name,
            arguments_json={} if event.name in ACCOUNT_LEARNING_TOOLS else event.arguments_json,
            output_text="Account history/learning result; content available through retained source citations"
            if event.name in ACCOUNT_LEARNING_TOOLS
            else event.output_text,
            is_error=event.is_error,
            group_id=event.group_id,
            created_at=event.created_at,
        )
        async with self._session_factory() as session:
            session.add(record)
            await session.commit()
        return record

    async def append_usage_snapshot(
        self,
        snapshot: AgentUsageSnapshot,
    ) -> AgentUsageRecord:
        record = AgentUsageRecord(
            session_id=snapshot.session_id,
            user_id=snapshot.user_id,
            agent_id=snapshot.agent_id,
            provider=snapshot.provider,
            model=snapshot.model,
            usage_type=snapshot.usage_type,
            input_tokens=snapshot.input_tokens,
            output_tokens=snapshot.output_tokens,
            total_tokens=snapshot.total_tokens,
            duration_seconds=snapshot.duration_seconds,
            audio_duration_seconds=snapshot.audio_duration_seconds,
            characters_count=snapshot.characters_count,
            total_requests=snapshot.total_requests,
            raw_usage_json=snapshot.raw_usage_json,
            created_at=snapshot.created_at,
        )
        async with self._session_factory() as session:
            session.add(record)
            await session.commit()
        return record

    async def append_runtime_event(
        self,
        event: AgentStateEvent,
    ) -> AgentRuntimeEventRecord:
        record = AgentRuntimeEventRecord(
            session_id=event.session_id,
            user_id=event.user_id,
            agent_id=event.agent_id,
            kind=event.kind.value,
            payload_json=event.payload_json,
            created_at=event.created_at,
        )
        async with self._session_factory() as session:
            session.add(record)
            await session.commit()
        return record

    async def list_transcript_items(
        self,
        *,
        session_id: str,
        user_id: str,
        agent_id: str,
        limit: int | None = None,
    ) -> tuple[AgentTranscriptRecord, ...]:
        statement = select(AgentTranscriptRecord).where(
            col(AgentTranscriptRecord.session_id) == session_id,
            col(AgentTranscriptRecord.user_id) == user_id,
            col(AgentTranscriptRecord.agent_id) == agent_id,
        )
        if limit is None:
            statement = statement.order_by(col(AgentTranscriptRecord.created_at).asc())
        else:
            statement = statement.order_by(col(AgentTranscriptRecord.created_at).desc()).limit(limit)
        async with self._session_factory() as session:
            result = await session.exec(statement)
            records = tuple(result.all())
        if limit is None:
            return records
        return tuple(reversed(records))

    async def list_transcript_items_for_sessions(
        self,
        *,
        session_ids: Sequence[str],
        user_id: str,
        limit_per_session: int,
    ) -> tuple[AgentTranscriptRecord, ...]:
        if not session_ids:
            return ()
        statement = (
            select(AgentTranscriptRecord)
            .where(
                col(AgentTranscriptRecord.session_id).in_(session_ids),
                col(AgentTranscriptRecord.user_id) == user_id,
            )
            .order_by(col(AgentTranscriptRecord.created_at).desc())
        )
        async with self._session_factory() as session:
            result = await session.exec(statement)
            records = tuple(result.all())
        return recent_records_per_session(records, limit_per_session)

    async def list_tool_events(
        self,
        *,
        session_id: str,
        user_id: str,
        agent_id: str,
        limit: int | None = None,
    ) -> tuple[AgentToolEventRecord, ...]:
        statement = select(AgentToolEventRecord).where(
            col(AgentToolEventRecord.session_id) == session_id,
            col(AgentToolEventRecord.user_id) == user_id,
            col(AgentToolEventRecord.agent_id) == agent_id,
        )
        if limit is None:
            statement = statement.order_by(col(AgentToolEventRecord.created_at).asc())
        else:
            statement = statement.order_by(col(AgentToolEventRecord.created_at).desc()).limit(limit)
        async with self._session_factory() as session:
            result = await session.exec(statement)
            records = tuple(result.all())
        if limit is None:
            return records
        return tuple(reversed(records))

    async def list_tool_events_for_sessions(
        self,
        *,
        session_ids: Sequence[str],
        user_id: str,
        limit_per_session: int,
    ) -> tuple[AgentToolEventRecord, ...]:
        if not session_ids:
            return ()
        statement = (
            select(AgentToolEventRecord)
            .where(
                col(AgentToolEventRecord.session_id).in_(session_ids),
                col(AgentToolEventRecord.user_id) == user_id,
            )
            .order_by(col(AgentToolEventRecord.created_at).desc())
        )
        async with self._session_factory() as session:
            result = await session.exec(statement)
            records = tuple(result.all())
        return recent_records_per_session(records, limit_per_session)

    async def list_runtime_events(
        self,
        *,
        session_id: str,
        user_id: str,
        agent_id: str,
        limit: int | None = None,
    ) -> tuple[AgentRuntimeEventRecord, ...]:
        statement = select(AgentRuntimeEventRecord).where(
            col(AgentRuntimeEventRecord.session_id) == session_id,
            col(AgentRuntimeEventRecord.user_id) == user_id,
            col(AgentRuntimeEventRecord.agent_id) == agent_id,
        )
        if limit is None:
            statement = statement.order_by(col(AgentRuntimeEventRecord.created_at).asc())
        else:
            statement = statement.order_by(col(AgentRuntimeEventRecord.created_at).desc()).limit(limit)
        async with self._session_factory() as session:
            result = await session.exec(statement)
            records = tuple(result.all())
        if limit is None:
            return records
        return tuple(reversed(records))

    async def list_runtime_events_for_sessions(
        self,
        *,
        session_ids: Sequence[str],
        user_id: str,
        limit_per_session: int,
    ) -> tuple[AgentRuntimeEventRecord, ...]:
        if not session_ids:
            return ()
        statement = (
            select(AgentRuntimeEventRecord)
            .where(
                col(AgentRuntimeEventRecord.session_id).in_(session_ids),
                col(AgentRuntimeEventRecord.user_id) == user_id,
            )
            .order_by(col(AgentRuntimeEventRecord.created_at).desc())
        )
        async with self._session_factory() as session:
            result = await session.exec(statement)
            records = tuple(result.all())
        return recent_records_per_session(records, limit_per_session)

    async def list_usage_events(
        self,
        *,
        session_id: str,
        user_id: str,
        agent_id: str,
        limit: int | None = None,
    ) -> tuple[AgentUsageRecord, ...]:
        statement = select(AgentUsageRecord).where(
            col(AgentUsageRecord.session_id) == session_id,
            col(AgentUsageRecord.user_id) == user_id,
            col(AgentUsageRecord.agent_id) == agent_id,
        )
        if limit is None:
            statement = statement.order_by(col(AgentUsageRecord.created_at).asc())
        else:
            statement = statement.order_by(col(AgentUsageRecord.created_at).desc()).limit(limit)
        async with self._session_factory() as session:
            result = await session.exec(statement)
            records = tuple(result.all())
        if limit is None:
            return records
        return tuple(reversed(records))

    async def _update_session_status(
        self,
        *,
        session_id: str,
        user_id: str,
        agent_id: str,
        status: AgentSessionStatus,
        close_reason: str | None,
        close_error_type: str | None,
        close_error_message: str | None,
    ) -> AgentSessionRecord | None:
        async with self._session_factory() as session:
            record = await session.get(AgentSessionRecord, session_id)
            if record is None or record.user_id != user_id or record.agent_id != agent_id:
                return None
            record.status = status.value
            record.ended_at = utc_now()
            record.updated_at = utc_now()
            record.close_reason = close_reason
            record.close_error_type = close_error_type
            record.close_error_message = close_error_message
            if record.owner_authenticated and close_error_type is not None:
                await enqueue_error_reflection(session, session=record, policy_json=record.effective_tool_policy_json)
            await session.commit()
            await session.refresh(record)
            return record

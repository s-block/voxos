from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from voxos.db.models import utc_now


class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current server datetime."""


class SystemClock:
    def now(self) -> datetime:
        return utc_now()


def ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def iso_datetime(value: datetime) -> str:
    return ensure_aware_utc(value).isoformat()


class CurrentDatetimeContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    current_datetime_utc: str = Field(min_length=1)
    timezone: Literal["UTC"] = "UTC"
    chat_started_at_utc: str | None = Field(default=None, min_length=1)
    voice_session_started_at_utc: str | None = Field(default=None, min_length=1)

    @classmethod
    def from_datetimes(
        cls,
        *,
        current_datetime: datetime,
        chat_started_at: datetime | None = None,
        voice_session_started_at: datetime | None = None,
    ) -> "CurrentDatetimeContext":
        return cls(
            current_datetime_utc=iso_datetime(current_datetime),
            chat_started_at_utc=(None if chat_started_at is None else iso_datetime(chat_started_at)),
            voice_session_started_at_utc=(
                None if voice_session_started_at is None else iso_datetime(voice_session_started_at)
            ),
        )


class TranscriptTimelineEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    position: int = Field(ge=1)
    role: Literal["user", "assistant"]
    created_at_utc: str = Field(min_length=1)

    @classmethod
    def from_datetime(
        cls,
        *,
        position: int,
        role: Literal["user", "assistant"],
        created_at: datetime,
    ) -> "TranscriptTimelineEntry":
        return cls(
            position=position,
            role=role,
            created_at_utc=iso_datetime(created_at),
        )


class ConversationTimelineContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    current_datetime_utc: str = Field(min_length=1)
    chat_started_at_utc: str | None = Field(default=None, min_length=1)
    last_prior_message_at_utc: str | None = Field(default=None, min_length=1)
    messages: tuple[TranscriptTimelineEntry, ...] = Field(default_factory=tuple)

    @classmethod
    def from_datetimes(
        cls,
        *,
        current_datetime: datetime,
        chat_started_at: datetime | None,
        prior_message_datetimes: Sequence[datetime],
        messages: tuple[TranscriptTimelineEntry, ...],
    ) -> "ConversationTimelineContext":
        return cls(
            current_datetime_utc=iso_datetime(current_datetime),
            chat_started_at_utc=(None if chat_started_at is None else iso_datetime(chat_started_at)),
            last_prior_message_at_utc=(
                None if not prior_message_datetimes else iso_datetime(prior_message_datetimes[-1])
            ),
            messages=messages,
        )


class CurrentDatetimeToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    current_datetime_utc: str = Field(min_length=1)
    timezone: Literal["UTC"] = "UTC"

    @classmethod
    def from_datetime(cls, current_datetime: datetime) -> "CurrentDatetimeToolResult":
        return cls(current_datetime_utc=iso_datetime(current_datetime))

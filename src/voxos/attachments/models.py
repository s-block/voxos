from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ImageMimeType = Literal["image/jpeg", "image/png"]


class AttachmentClientSource(StrEnum):
    WEB = "web"
    FLUTTER = "flutter"
    AGENT = "agent"


class AttachmentState(StrEnum):
    PENDING = "pending"
    READY = "ready"
    ATTACHED = "attached"
    DELETED = "deleted"
    FAILED = "failed"


class AttachmentVariant(StrEnum):
    FULL = "full"
    THUMBNAIL = "thumbnail"


class AttachmentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    mime_type: ImageMimeType
    byte_size: int = Field(ge=1)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    position: int | None = Field(default=None, ge=0)


class NormalizedImage(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True)

    content: bytes
    thumbnail: bytes
    mime_type: ImageMimeType
    byte_size: int = Field(ge=1)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    sha256: str = Field(min_length=64, max_length=64)


class ClaimedTurn(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True)

    transcript_id: str = Field(min_length=1, max_length=36)
    created: bool

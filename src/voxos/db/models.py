import uuid
from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_record_id() -> str:
    return str(uuid.uuid4())

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

CLIENT_LOCATION_METADATA_KEY = "voxos_location"
CLIENT_LOCATION_MAX_AGE = timedelta(minutes=15)
CLIENT_LOCATION_FUTURE_TOLERANCE = timedelta(minutes=2)


class ClientLocation(BaseModel):
    """A user-approved, request-scoped WGS84 device location snapshot."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
    )

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    horizontal_accuracy_m: float = Field(ge=0, le=100_000)
    captured_at: datetime

    @field_validator("captured_at")
    @classmethod
    def normalize_captured_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            message = "captured_at must include a timezone"
            raise ValueError(message)
        return value.astimezone(UTC)


def fresh_client_location(
    location: ClientLocation | None,
    *,
    now: datetime,
    max_age: timedelta = CLIENT_LOCATION_MAX_AGE,
    future_tolerance: timedelta = CLIENT_LOCATION_FUTURE_TOLERANCE,
) -> ClientLocation | None:
    if location is None:
        return None
    normalized_now = now.replace(tzinfo=UTC) if now.tzinfo is None or now.utcoffset() is None else now.astimezone(UTC)
    age = normalized_now - location.captured_at
    if age > max_age or age < -future_tolerance:
        return None
    return location


def client_location_from_metadata(
    metadata: Mapping[str, object],
    *,
    now: datetime,
) -> ClientLocation | None:
    candidate = metadata.get(CLIENT_LOCATION_METADATA_KEY)
    if candidate is None:
        return None
    try:
        location = ClientLocation.model_validate(candidate)
    except ValidationError:
        return None
    return fresh_client_location(location, now=now)


def metadata_without_client_location(
    metadata: Mapping[str, object],
) -> dict[str, object]:
    return {key: value for key, value in metadata.items() if key != CLIENT_LOCATION_METADATA_KEY}

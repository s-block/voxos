from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from voxos.location.models import (
    CLIENT_LOCATION_METADATA_KEY,
    ClientLocation,
    client_location_from_metadata,
    fresh_client_location,
    metadata_without_client_location,
)


def test_client_location_validates_wgs84_ranges_and_timezone() -> None:
    location = ClientLocation(
        latitude=42.4304,
        longitude=19.2594,
        horizontal_accuracy_m=12.5,
        captured_at=datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
    )

    assert location.latitude == 42.4304
    assert location.captured_at.tzinfo == UTC

    with pytest.raises(ValidationError):
        ClientLocation(
            latitude=91,
            longitude=19.2594,
            horizontal_accuracy_m=12.5,
            captured_at=datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
        )
    with pytest.raises(ValidationError, match="timezone"):
        ClientLocation(
            latitude=42.4304,
            longitude=19.2594,
            horizontal_accuracy_m=12.5,
            captured_at=datetime(2026, 8, 30, 12, 0),
        )


def test_fresh_client_location_rejects_old_and_implausibly_future_snapshots() -> None:
    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)

    def location(captured_at: datetime) -> ClientLocation:
        return ClientLocation(
            latitude=42.4304,
            longitude=19.2594,
            horizontal_accuracy_m=20,
            captured_at=captured_at,
        )

    assert fresh_client_location(location(now - timedelta(minutes=5)), now=now) is not None
    assert fresh_client_location(location(now - timedelta(minutes=16)), now=now) is None
    assert fresh_client_location(location(now + timedelta(minutes=3)), now=now) is None


def test_metadata_location_is_optional_and_removed_before_persistence() -> None:
    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    metadata = {
        "device": "browser",
        CLIENT_LOCATION_METADATA_KEY: {
            "latitude": 42.4304,
            "longitude": 19.2594,
            "horizontal_accuracy_m": 25,
            "captured_at": now.isoformat(),
        },
    }

    location = client_location_from_metadata(metadata, now=now)

    assert location is not None
    assert location.longitude == 19.2594
    assert metadata_without_client_location(metadata) == {"device": "browser"}
    assert (
        client_location_from_metadata(
            {CLIENT_LOCATION_METADATA_KEY: {"latitude": "invalid"}},
            now=now,
        )
        is None
    )

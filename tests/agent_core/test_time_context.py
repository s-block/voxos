from datetime import UTC, datetime, timedelta, timezone

from voxos.agent_core.time_context import ensure_aware_utc, iso_datetime


def test_iso_datetime_keeps_aware_utc_datetime() -> None:
    value = datetime(2026, 7, 9, 12, 34, 56, tzinfo=UTC)

    assert iso_datetime(value) == "2026-07-09T12:34:56+00:00"


def test_iso_datetime_converts_aware_non_utc_datetime() -> None:
    value = datetime(
        2026,
        7,
        9,
        13,
        34,
        56,
        tzinfo=timezone(timedelta(hours=1)),
    )

    assert iso_datetime(value) == "2026-07-09T12:34:56+00:00"


def test_ensure_aware_utc_treats_naive_datetime_as_utc() -> None:
    value = datetime(2026, 7, 9, 12, 34, 56)

    normalized = ensure_aware_utc(value)

    assert normalized.tzinfo == UTC
    assert normalized.isoformat() == "2026-07-09T12:34:56+00:00"

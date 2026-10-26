import pytest
from pydantic import ValidationError

from voxos.whatsapp.settings import WhatsAppWorkerSettings


def test_worker_settings_defaults_are_lifecycle_safe() -> None:
    settings = WhatsAppWorkerSettings()

    assert settings.whatsapp_worker_lease_ttl_seconds > (
        max(
            settings.whatsapp_worker_shutdown_timeout_seconds,
            settings.whatsapp_bridge_request_timeout_seconds,
        )
        + settings.whatsapp_worker_poll_interval_seconds * 2
    )
    assert settings.whatsapp_max_sessions_per_worker > 0
    assert settings.whatsapp_worker_restart_after_seconds > max(
        settings.whatsapp_health_freshness_seconds,
        settings.whatsapp_bridge_request_timeout_seconds,
    )


def test_worker_settings_reject_short_lease() -> None:
    with pytest.raises(
        ValidationError,
        match="request and shutdown timeouts plus two poll",
    ):
        WhatsAppWorkerSettings(
            whatsapp_worker_lease_ttl_seconds=10.0,
            whatsapp_worker_shutdown_timeout_seconds=9.5,
            whatsapp_worker_poll_interval_seconds=0.5,
        )


def test_worker_settings_reject_stale_health_window() -> None:
    with pytest.raises(ValidationError, match="must exceed"):
        WhatsAppWorkerSettings(
            whatsapp_health_freshness_seconds=0.5,
            whatsapp_worker_poll_interval_seconds=0.5,
        )


def test_worker_settings_reject_restart_window_shorter_than_worker_timeouts() -> None:
    with pytest.raises(ValidationError, match="must exceed the health freshness"):
        WhatsAppWorkerSettings(
            whatsapp_worker_restart_after_seconds=20.0,
            whatsapp_bridge_request_timeout_seconds=20.0,
        )

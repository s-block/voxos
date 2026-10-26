from unittest.mock import patch

from voxos.browser_accounts.models import (
    BrowserAccountAuditEventKind,
    BrowserAccountErrorCategory,
)
from voxos.browser_accounts.telemetry import (
    record_browser_account_event,
    record_remote_deletion_backlog,
)


def test_lifecycle_telemetry_contains_only_safe_categories() -> None:
    with (
        patch(
            "voxos.browser_accounts.telemetry.newrelic.agent.record_custom_metric",
        ) as record_metric,
        patch(
            "voxos.browser_accounts.telemetry.newrelic.agent.record_custom_event",
        ) as record_event,
    ):
        record_browser_account_event(
            event_kind=BrowserAccountAuditEventKind.PROVIDER_ERROR,
            outcome="failed",
            error_category=BrowserAccountErrorCategory.PROVIDER_UNAVAILABLE,
        )

    record_metric.assert_called_once_with(
        "Custom/Voxos/BrowserAccounts/provider_error",
        1,
    )
    record_event.assert_called_once_with(
        "VoxosBrowserAccountLifecycle",
        {
            "event_kind": "provider_error",
            "outcome": "failed",
            "error_category": "provider_unavailable",
        },
    )


def test_remote_deletion_backlog_records_only_the_count() -> None:
    with patch(
        "voxos.browser_accounts.telemetry.newrelic.agent.record_custom_metric",
    ) as record_metric:
        record_remote_deletion_backlog(3)

    record_metric.assert_called_once_with(
        "Custom/Voxos/BrowserAccounts/RemoteDeletionBacklog",
        3,
    )

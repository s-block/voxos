import logging

import newrelic.agent

from voxos.browser_accounts.models import (
    BrowserAccountAuditEventKind,
    BrowserAccountErrorCategory,
)

logger = logging.getLogger(__name__)


def record_browser_account_event(
    *,
    event_kind: BrowserAccountAuditEventKind,
    outcome: str,
    error_category: BrowserAccountErrorCategory | None,
) -> None:
    """Record a privacy-safe lifecycle signal without tenant or browser data."""
    _record_custom_metric(
        f"Custom/Voxos/BrowserAccounts/{event_kind.value}",
        1,
    )
    params: dict[str, object] = {
        "event_kind": event_kind.value,
        "outcome": outcome,
    }
    if error_category is not None:
        params["error_category"] = error_category.value
    _record_custom_event("VoxosBrowserAccountLifecycle", params)


def record_remote_deletion_backlog(pending_count: int) -> None:
    _record_custom_metric(
        "Custom/Voxos/BrowserAccounts/RemoteDeletionBacklog",
        pending_count,
    )


def _record_custom_metric(name: str, value: int | float) -> None:
    try:
        newrelic.agent.record_custom_metric(name, value)
    except Exception:
        logger.debug("Browser Account metric recording failed", exc_info=True)


def _record_custom_event(event_type: str, params: dict[str, object]) -> None:
    try:
        newrelic.agent.record_custom_event(event_type, params)
    except Exception:
        logger.debug("Browser Account event recording failed", exc_info=True)

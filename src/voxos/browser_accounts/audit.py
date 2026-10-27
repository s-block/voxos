from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.browser_accounts.models import (
    BrowserAccountAuditEventKind,
    BrowserAccountErrorCategory,
)
from voxos.browser_accounts.records import BrowserAccountAuditEventRecord
from voxos.browser_accounts.telemetry import record_browser_account_event


def add_browser_account_audit_event(
    *,
    session: AsyncSession,
    user_id: str,
    browser_account_id: str,
    event_kind: BrowserAccountAuditEventKind,
    outcome: str,
    authorization_id: str | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    error_category: BrowserAccountErrorCategory | None = None,
) -> BrowserAccountAuditEventRecord:
    record = BrowserAccountAuditEventRecord(
        user_id=user_id,
        browser_account_id=browser_account_id,
        authorization_id=authorization_id,
        agent_id=agent_id,
        run_id=run_id,
        session_id=session_id,
        event_kind=event_kind.value,
        outcome=outcome,
        error_category=None if error_category is None else error_category.value,
    )
    session.add(record)
    record_browser_account_event(
        event_kind=event_kind,
        outcome=outcome,
        error_category=error_category,
    )
    return record

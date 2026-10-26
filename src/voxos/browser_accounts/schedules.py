from dataclasses import dataclass

from sqlmodel import col, select

from voxos.browser_accounts.errors import BrowserAccountAuthorizationError
from voxos.browser_accounts.models import (
    BrowserAccountAuthenticationMode,
    BrowserAccountProvider,
    BrowserAccountStatus,
)
from voxos.browser_accounts.records import (
    AgentBrowserAccountGrantRecord,
    BrowserAccountRecord,
)
from voxos.db.session import AsyncSessionFactory

READY_STORED_CREDENTIAL_ACCOUNT_REQUIRED = "A ready stored-credential Browser Account is required"
AGENT_ACCOUNT_GRANT_REQUIRED = "Agent is not assigned to this Browser Account"


@dataclass(frozen=True, slots=True)
class BrowserAccountScheduleAuthorizer:
    session_factory: AsyncSessionFactory

    async def require_account(
        self,
        *,
        user_id: str,
        agent_id: str,
        browser_account_id: str,
    ) -> None:
        async with self.session_factory() as session:
            account_statement = select(BrowserAccountRecord).where(
                col(BrowserAccountRecord.id) == browser_account_id,
                col(BrowserAccountRecord.user_id) == user_id,
                col(BrowserAccountRecord.deleted_at).is_(None),
            )
            account_result = await session.exec(account_statement)
            account = account_result.one_or_none()
            if (
                account is None
                or BrowserAccountStatus(account.status) != BrowserAccountStatus.READY
                or BrowserAccountProvider(account.provider)
                not in {
                    BrowserAccountProvider.BROWSERLESS,
                    BrowserAccountProvider.STAGEHAND_LOCAL,
                }
                or BrowserAccountAuthenticationMode(account.authentication_mode)
                != BrowserAccountAuthenticationMode.STORED_CREDENTIALS
            ):
                raise BrowserAccountAuthorizationError(
                    READY_STORED_CREDENTIAL_ACCOUNT_REQUIRED,
                )
            grant_statement = select(AgentBrowserAccountGrantRecord.id).where(
                col(AgentBrowserAccountGrantRecord.user_id) == user_id,
                col(AgentBrowserAccountGrantRecord.agent_id) == agent_id,
                col(AgentBrowserAccountGrantRecord.browser_account_id) == browser_account_id,
            )
            grant_result = await session.exec(grant_statement)
            if grant_result.one_or_none() is None:
                raise BrowserAccountAuthorizationError(
                    AGENT_ACCOUNT_GRANT_REQUIRED,
                )

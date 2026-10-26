import pytest

from voxos.agent_identity.records import UserAgentRecord
from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.schedule_models import AgentScheduleCreateRequest, ScheduleKind
from voxos.agent_runtime.schedule_store import AgentScheduleStore
from voxos.agent_runtime.storage import SqlModelAgentRuntimeStore
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
from voxos.browser_accounts.schedules import BrowserAccountScheduleAuthorizer
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.text.run_chat_service import RunChatService


@pytest.mark.asyncio
async def test_schedule_account_authority_is_tenant_and_agent_scoped(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        session.add(
            UserAgentRecord(
                id="scheduled-agent",
                owner_user_id="scheduled-user",
                slug="scheduled-agent",
                display_name="Scheduled Agent",
            ),
        )
        session.add(
            UserAgentRecord(
                id="other-agent",
                owner_user_id="scheduled-user",
                slug="other-agent",
                display_name="Other Agent",
            ),
        )
        account = BrowserAccountRecord(
            id="scheduled-account",
            user_id="scheduled-user",
            label="Billing",
            provider=BrowserAccountProvider.BROWSERLESS.value,
            provider_connection_id="browserless-connection",
            primary_origin="https://billing.example.com",
            authentication_mode=(BrowserAccountAuthenticationMode.STORED_CREDENTIALS.value),
            status=BrowserAccountStatus.READY.value,
        )
        session.add(account)
        session.add(
            AgentBrowserAccountGrantRecord(
                user_id="scheduled-user",
                agent_id="scheduled-agent",
                browser_account_id=account.id,
            ),
        )
        await session.commit()

    authorizer = BrowserAccountScheduleAuthorizer(sqlite_session_factory)
    await authorizer.require_account(
        user_id="scheduled-user",
        agent_id="scheduled-agent",
        browser_account_id="scheduled-account",
    )
    with pytest.raises(BrowserAccountAuthorizationError):
        await authorizer.require_account(
            user_id="another-user",
            agent_id="scheduled-agent",
            browser_account_id="scheduled-account",
        )
    with pytest.raises(BrowserAccountAuthorizationError):
        await authorizer.require_account(
            user_id="scheduled-user",
            agent_id="other-agent",
            browser_account_id="scheduled-account",
        )


@pytest.mark.asyncio
async def test_schedule_enqueue_snapshots_browser_account_onto_run(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(sqlite_session_factory)
    agent = await identity_service.get_or_create_default_agent("snapshot-user")
    async with sqlite_session_factory() as session:
        account = BrowserAccountRecord(
            id="snapshot-account",
            user_id="snapshot-user",
            label="Snapshot billing",
            provider=BrowserAccountProvider.BROWSERLESS.value,
            provider_connection_id="browserless-connection",
            primary_origin="https://billing.example.com",
            authentication_mode=BrowserAccountAuthenticationMode.STORED_CREDENTIALS.value,
            status=BrowserAccountStatus.READY.value,
        )
        session.add(account)
        session.add(
            AgentBrowserAccountGrantRecord(
                user_id="snapshot-user",
                agent_id=agent.id,
                browser_account_id=account.id,
            ),
        )
        await session.commit()
    run_store = SqlModelAgentRunStore(sqlite_session_factory)
    runtime_store = SqlModelAgentRuntimeStore(sqlite_session_factory)
    schedule_store = AgentScheduleStore(
        session_factory=sqlite_session_factory,
        run_store=run_store,
        identity_service=identity_service,
        run_chat_service=RunChatService(
            session_factory=sqlite_session_factory,
            runtime_store=runtime_store,
            run_store=run_store,
        ),
        browser_account_authorizer=BrowserAccountScheduleAuthorizer(
            sqlite_session_factory,
        ),
    )
    await schedule_store.create_schedule(
        user_id="snapshot-user",
        request=AgentScheduleCreateRequest(
            agent_id=agent.id,
            browser_account_id=account.id,
            name="Check bill",
            kind=ScheduleKind.ONE_SHOT,
            run_at=utc_now(),
            payload_json={"text": "Read the bill"},
        ),
    )

    await schedule_store.enqueue_due_schedules()
    runs = await run_store.list_runs(user_id="snapshot-user")

    assert len(runs) == 1
    assert runs[0].browser_account_id == account.id

import asyncio
from datetime import timedelta

import pytest
from pydantic import SecretStr
from sqlmodel import select

from voxos.agent_identity.records import UserAgentRecord
from voxos.agent_runtime.models import AgentTranscriptRole, AgentTranscriptSource
from voxos.agent_runtime.records import (
    AgentRunRecord,
    AgentScheduleRecord,
    AgentSessionRecord,
    AgentTranscriptRecord,
)
from voxos.browser_accounts.errors import (
    BrowserAccountAuthorizationError,
    BrowserAccountConflictError,
    BrowserAccountNotFoundError,
    BrowserAccountProviderError,
)
from voxos.browser_accounts.models import (
    BrowserAccountApprovalRequest,
    BrowserAccountAuditEventKind,
    BrowserAccountAuthenticationMode,
    BrowserAccountCreateRequest,
    BrowserAccountErrorCategory,
    BrowserAccountProvider,
    BrowserAccountResponse,
    BrowserAccountStatus,
    BrowserAccountUpdateRequest,
)
from voxos.browser_accounts.providers.base import (
    BrowserProviderCapabilities,
    BrowserProviderCredential,
    BrowserProviderOperationError,
    BrowserProviderSetupSession,
    BrowserProviderTaskSession,
)
from voxos.browser_accounts.records import (
    BrowserAccountAuditEventRecord,
    BrowserAccountLeaseRecord,
    BrowserAccountRecord,
    BrowserAccountRunAuthorizationRecord,
)
from voxos.browser_accounts.secrets import (
    BrowserAccountSecretStore,
    OpaqueBrowserProfileHandle,
    OpaqueBrowserSessionHandle,
)
from voxos.browser_accounts.services import (
    UNUSABLE_BROWSER_TASK_RESULT,
    BrowserAccountService,
)
from voxos.browser_accounts.settings import BrowserAccountSettings
from voxos.browser_runtime.contracts import (
    BrowserTaskRequest,
    BrowserTaskResult,
    BrowserTaskVariable,
)
from voxos.credentials.records import UserSecretRecord
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.mcp.records import UserMcpServerRecord

TEST_USER_ID = "browser-user"
TEST_AGENT_ID = "browser-agent"
TEST_CHAT_ID = "browser-chat"


@pytest.mark.asyncio
async def test_local_account_is_ready_without_an_mcp_connection(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    service = BrowserAccountService(
        session_factory=sqlite_session_factory,
        provider=FakeBrowserlessProvider(),
        credential_resolver=FakeBrowserlessCredentialResolver(),
        secret_store=BrowserAccountSecretStore(user_secret_vault),
        settings=BrowserAccountSettings(enabled=True),
    )

    account = await service.create_account(
        user_id=TEST_USER_ID,
        request=BrowserAccountCreateRequest(
            label="Local billing",
            primary_origin="https://billing.example.com",
            provider=BrowserAccountProvider.STAGEHAND_LOCAL,
            username=SecretStr("billing@example.com"),
            password=SecretStr("website-password"),
        ),
    )

    assert account.provider == BrowserAccountProvider.STAGEHAND_LOCAL
    assert account.provider_connection_id is None
    assert account.authentication_mode == BrowserAccountAuthenticationMode.STORED_CREDENTIALS
    assert account.status == BrowserAccountStatus.READY
    assert account.has_credentials is True


class FakeCredentialResolver:
    async def require_connection(
        self,
        *,
        session: object,
        user_id: str,
        connection_id: str,
        require_active: bool = True,
    ) -> UserMcpServerRecord:
        del session, require_active
        assert user_id == TEST_USER_ID
        return UserMcpServerRecord(
            id=connection_id,
            user_id=user_id,
            label="Browser Use",
            url="https://api.browser-use.com/v3/mcp",
            transport="streamable_http",
            auth_type="api_key_header",
        )

    async def resolve_credential(
        self,
        *,
        session: object,
        user_id: str,
        connection_id: str,
        require_active: bool = True,
    ) -> BrowserProviderCredential:
        await self.require_connection(
            session=session,
            user_id=user_id,
            connection_id=connection_id,
            require_active=require_active,
        )
        return BrowserProviderCredential(api_key=SecretStr("provider-key"))


class FakeBrowserlessCredentialResolver(FakeCredentialResolver):
    async def resolve_credential(
        self,
        *,
        session: object,
        user_id: str,
        connection_id: str,
        require_active: bool = True,
    ) -> BrowserProviderCredential:
        await self.require_connection(
            session=session,
            user_id=user_id,
            connection_id=connection_id,
            require_active=require_active,
        )
        return BrowserProviderCredential(
            api_key=SecretStr("browserless-key"),
            provider=BrowserAccountProvider.BROWSERLESS,
        )


class FakeBrowserProvider:
    capabilities = BrowserProviderCapabilities(
        human_live_view=True,
        managed_profiles=True,
        remote_profile_deletion=True,
        recording_can_be_disabled=True,
        exact_domain_restriction=True,
        persistent_state=True,
        native_secret_injection=False,
        concurrent_profile_sessions=1,
    )

    def __init__(
        self,
        task_result: BrowserTaskResult | None = None,
        task_error_category: BrowserAccountErrorCategory | None = None,
    ) -> None:
        self.deleted_profiles = 0
        self.fail_profile_deletion = False
        self.stopped_sessions = 0
        self.tasks: list[tuple[str, str, tuple[str, ...], int]] = []
        self.setup_country_codes: list[str] = []
        self.task_country_codes: list[str] = []
        self.task_result = task_result or BrowserTaskResult(
            output_text="Invoice total: £42",
            successful=True,
        )
        self.task_error_category = task_error_category

    async def create_profile(
        self,
        *,
        credential: BrowserProviderCredential,
        account_label: str,
        external_user_reference: str,
    ) -> OpaqueBrowserProfileHandle:
        del credential, account_label, external_user_reference
        return OpaqueBrowserProfileHandle(SecretStr("opaque-profile-handle"))

    async def delete_profile(
        self,
        *,
        credential: BrowserProviderCredential,
        profile: OpaqueBrowserProfileHandle,
    ) -> None:
        del credential, profile
        if self.fail_profile_deletion:
            raise BrowserProviderOperationError(
                BrowserAccountErrorCategory.PROVIDER_UNAVAILABLE,
            )
        self.deleted_profiles += 1

    async def profile_exists(
        self,
        *,
        credential: BrowserProviderCredential,
        profile: OpaqueBrowserProfileHandle,
    ) -> bool:
        del credential, profile
        return True

    async def start_setup_session(
        self,
        *,
        credential: BrowserProviderCredential,
        profile: OpaqueBrowserProfileHandle,
        browser_country_code: str,
    ) -> BrowserProviderSetupSession:
        del credential, profile
        self.setup_country_codes.append(browser_country_code)
        return BrowserProviderSetupSession(
            handle=OpaqueBrowserSessionHandle(SecretStr("opaque-setup-session")),
            live_url="https://live.browser-use.com/short-lived-token",
            expires_at=utc_now() + timedelta(minutes=10),
        )

    async def stop_session(
        self,
        *,
        credential: BrowserProviderCredential,
        session: OpaqueBrowserSessionHandle,
    ) -> None:
        del credential, session
        self.stopped_sessions += 1

    async def start_task(
        self,
        *,
        credential: BrowserProviderCredential,
        profile: OpaqueBrowserProfileHandle | None,
        request: BrowserTaskRequest,
    ) -> BrowserProviderTaskSession:
        del credential, profile
        self.task_country_codes.append(request.browser_country_code)
        self.tasks.append(
            (
                request.instruction,
                request.start_url,
                request.allowed_origins,
                request.max_steps,
            ),
        )
        return BrowserProviderTaskSession(
            task_handle=SecretStr("opaque-task"),
            session_handle=OpaqueBrowserSessionHandle(SecretStr("opaque-session")),
        )

    async def wait_for_task(
        self,
        *,
        credential: BrowserProviderCredential,
        task: BrowserProviderTaskSession,
    ) -> BrowserTaskResult:
        del credential, task
        if self.task_error_category is not None:
            raise BrowserProviderOperationError(self.task_error_category)
        return self.task_result


class FakeBrowserlessProvider(FakeBrowserProvider):
    def __init__(self) -> None:
        super().__init__()
        self.received_variables: dict[str, BrowserTaskVariable] = {}

    async def start_task(
        self,
        *,
        credential: BrowserProviderCredential,
        profile: OpaqueBrowserProfileHandle | None,
        request: BrowserTaskRequest,
    ) -> BrowserProviderTaskSession:
        del credential, profile
        self.received_variables = dict(request.variables)
        self.task_country_codes.append(request.browser_country_code)
        self.tasks.append(
            (
                request.instruction,
                request.start_url,
                request.allowed_origins,
                request.max_steps,
            ),
        )
        return BrowserProviderTaskSession(
            task_handle=SecretStr("opaque-browserless-task"),
            session_handle=OpaqueBrowserSessionHandle(
                SecretStr("opaque-browserless-session"),
            ),
        )


class StartFailingBrowserlessProvider(FakeBrowserlessProvider):
    async def start_task(
        self,
        *,
        credential: BrowserProviderCredential,
        profile: OpaqueBrowserProfileHandle | None,
        request: BrowserTaskRequest,
    ) -> BrowserProviderTaskSession:
        del credential, profile, request
        raise BrowserProviderOperationError(
            BrowserAccountErrorCategory.PROVIDER_REQUEST_INVALID,
        )


class BlockingBrowserlessProvider(FakeBrowserlessProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def wait_for_task(
        self,
        *,
        credential: BrowserProviderCredential,
        task: BrowserProviderTaskSession,
    ) -> BrowserTaskResult:
        del credential, task
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError


class CancellationBlockingBrowserlessProvider(FakeBrowserlessProvider):
    def __init__(self) -> None:
        super().__init__()
        self.wait_started = asyncio.Event()
        self.stop_started = asyncio.Event()
        self.allow_stop = asyncio.Event()
        self.wait_calls = 0

    async def wait_for_task(
        self,
        *,
        credential: BrowserProviderCredential,
        task: BrowserProviderTaskSession,
    ) -> BrowserTaskResult:
        del credential, task
        self.wait_calls += 1
        if self.wait_calls == 1:
            self.wait_started.set()
            await asyncio.Event().wait()
            raise AssertionError
        return self.task_result

    async def stop_session(
        self,
        *,
        credential: BrowserProviderCredential,
        session: OpaqueBrowserSessionHandle,
    ) -> None:
        del credential, session
        self.stopped_sessions += 1
        if self.stopped_sessions == 1:
            self.stop_started.set()
            await self.allow_stop.wait()


@pytest.mark.asyncio
async def test_browser_account_details_update_changes_retrieval_origins(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_agent_and_chat(sqlite_session_factory)
    provider = FakeBrowserlessProvider()
    service = BrowserAccountService(
        session_factory=sqlite_session_factory,
        provider=provider,
        credential_resolver=FakeBrowserlessCredentialResolver(),
        secret_store=BrowserAccountSecretStore(user_secret_vault),
        settings=BrowserAccountSettings(enabled=True),
    )
    account = await service.create_account(
        user_id=TEST_USER_ID,
        request=BrowserAccountCreateRequest(
            label="Energy",
            primary_origin="https://eonnext.com",
            provider_connection_id="browserless-connection",
            agent_ids=(TEST_AGENT_ID,),
            username=SecretStr("billing@example.com"),
            password=SecretStr("website-password"),
        ),
    )

    updated = await service.update_account(
        user_id=TEST_USER_ID,
        account_id=account.id,
        request=BrowserAccountUpdateRequest(
            label="Eon Next",
            primary_origin="https://eonnext.com",
            authentication_origins=("https://auth.eonnext.com",),
        ),
    )

    assert updated.label == "Eon Next"
    assert updated.authentication_origins == ("https://auth.eonnext.com",)
    assert updated.has_credentials is True

    await seed_chat_run(
        sqlite_session_factory,
        run_id="updated-origins-run",
        client_turn_id="updated-origins-turn",
        task="Read the next payment date",
    )
    result = await service.execute_assigned_run_task(
        user_id=TEST_USER_ID,
        agent_id=TEST_AGENT_ID,
        account_id=account.id,
        session_id=TEST_CHAT_ID,
        run_id="updated-origins-run",
        instruction="Read the next payment date",
    )

    assert result.successful is True
    assert provider.tasks[-1][1:] == (
        "https://eonnext.com",
        ("https://eonnext.com", "https://auth.eonnext.com"),
        40,
    )
    assert provider.task_country_codes[-1] == "GB"
    async with sqlite_session_factory() as session:
        audit_events = tuple(
            (await session.exec(select(BrowserAccountAuditEventRecord))).all(),
        )
    assert any(
        event.event_kind == BrowserAccountAuditEventKind.ACCOUNT_UPDATED.value
        and event.browser_account_id == account.id
        for event in audit_events
    )
    with pytest.raises(BrowserAccountNotFoundError):
        await service.update_account(
            user_id="another-user",
            account_id=account.id,
            request=BrowserAccountUpdateRequest(
                label="Forbidden update",
                primary_origin="https://example.com",
            ),
        )


@pytest.mark.asyncio
async def test_browser_country_change_requires_managed_profile_setup(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_agent_and_chat(sqlite_session_factory)
    provider = FakeBrowserProvider()
    service = browser_account_service(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        provider=provider,
    )
    account = await service.create_account(
        user_id=TEST_USER_ID,
        request=BrowserAccountCreateRequest(
            label="Billing",
            primary_origin="https://billing.example.com",
            provider_connection_id="browser-use-connection",
            agent_ids=(TEST_AGENT_ID,),
        ),
    )
    await service.start_setup(user_id=TEST_USER_ID, account_id=account.id)
    await service.complete_setup(user_id=TEST_USER_ID, account_id=account.id)

    updated = await service.update_account(
        user_id=TEST_USER_ID,
        account_id=account.id,
        request=BrowserAccountUpdateRequest(
            label=account.label,
            primary_origin=account.primary_origin,
            browser_country_code="US",
        ),
    )

    assert updated.browser_country_code == "US"
    assert updated.status == BrowserAccountStatus.SETUP_REQUIRED
    assert updated.last_verified_at is None

    await service.start_setup(user_id=TEST_USER_ID, account_id=account.id)
    assert provider.setup_country_codes == ["GB", "US"]
    with pytest.raises(BrowserAccountConflictError):
        await service.update_account(
            user_id=TEST_USER_ID,
            account_id=account.id,
            request=BrowserAccountUpdateRequest(
                label=account.label,
                primary_origin=account.primary_origin,
                browser_country_code="CA",
            ),
        )


@pytest.mark.asyncio
async def test_browserless_credentials_are_vaulted_and_used_without_api_exposure(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_agent_and_chat(sqlite_session_factory)
    provider = FakeBrowserlessProvider()
    service = BrowserAccountService(
        session_factory=sqlite_session_factory,
        provider=provider,
        credential_resolver=FakeBrowserlessCredentialResolver(),
        secret_store=BrowserAccountSecretStore(user_secret_vault),
        settings=BrowserAccountSettings(enabled=True),
    )
    account = await service.create_account(
        user_id=TEST_USER_ID,
        request=BrowserAccountCreateRequest(
            label="Browserless billing",
            primary_origin="https://billing.example.com",
            provider_connection_id="browserless-connection",
            agent_ids=(TEST_AGENT_ID,),
            username=SecretStr("billing@example.com"),
            password=SecretStr("website-password"),
        ),
    )

    assert account.provider == BrowserAccountProvider.BROWSERLESS
    assert account.status == BrowserAccountStatus.READY
    assert account.has_credentials is True
    serialized = account.model_dump_json()
    assert "billing@example.com" not in serialized
    assert "website-password" not in serialized

    await seed_chat_run(
        sqlite_session_factory,
        run_id="browserless-run",
        client_turn_id="browserless-turn",
    )
    authorization = await service.approve_for_chat(
        user_id=TEST_USER_ID,
        request=BrowserAccountApprovalRequest(
            account_id=account.id,
            chat_id=TEST_CHAT_ID,
            client_turn_id="browserless-turn",
            task_summary="Read the latest invoice total",
        ),
    )
    await service.execute_authorized_task(
        user_id=TEST_USER_ID,
        agent_id=TEST_AGENT_ID,
        run_id="browserless-run",
        session_id=TEST_CHAT_ID,
        expected_authorization_id=authorization.id,
    )

    assert provider.received_variables["login_identifier"].value.get_secret_value() == "billing@example.com"
    assert provider.received_variables["login_password"].value.get_secret_value() == "website-password"
    async with sqlite_session_factory() as session:
        records = tuple((await session.exec(select(UserSecretRecord))).all())
        assert all(b"website-password" not in record.ciphertext for record in records)
        assert (
            await service.secret_store.load_account_credentials(
                session=session,
                user_id="another-user",
                browser_account_id=account.id,
            )
            is None
        )


@pytest.mark.asyncio
async def test_assigned_browserless_account_uses_exact_run_request_without_chat_approval(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_agent_and_chat(sqlite_session_factory)
    provider = FakeBrowserlessProvider()
    service = BrowserAccountService(
        session_factory=sqlite_session_factory,
        provider=provider,
        credential_resolver=FakeBrowserlessCredentialResolver(),
        secret_store=BrowserAccountSecretStore(user_secret_vault),
        settings=BrowserAccountSettings(enabled=True),
    )
    account = await service.create_account(
        user_id=TEST_USER_ID,
        request=BrowserAccountCreateRequest(
            label="Eon Next",
            primary_origin="https://eonnext.com",
            provider_connection_id="browserless-connection",
            agent_ids=(TEST_AGENT_ID,),
            username=SecretStr("billing@example.com"),
            password=SecretStr("website-password"),
        ),
    )
    available = await service.list_assigned_ready_accounts(
        user_id=TEST_USER_ID,
        agent_id=TEST_AGENT_ID,
    )
    assert len(available) == 1
    assert available[0].account_id == account.id
    assert available[0].label == "Eon Next"
    assert available[0].execution_tool_name == "browserless_agent"

    await seed_chat_run(
        sqlite_session_factory,
        run_id="automatic-run",
        client_turn_id="automatic-turn",
        task="Sign in to Eon and get my latest bill total",
    )
    result = await service.execute_assigned_run_task(
        user_id=TEST_USER_ID,
        agent_id=TEST_AGENT_ID,
        account_id=account.id,
        session_id=TEST_CHAT_ID,
        run_id="automatic-run",
        instruction="Sign in to Eon and get my latest bill total",
    )

    assert result.successful is True
    assert provider.tasks == [
        (
            "Sign in to Eon and get my latest bill total",
            "https://eonnext.com",
            ("https://eonnext.com",),
            40,
        ),
    ]
    assert provider.received_variables["login_identifier"].value.get_secret_value() == "billing@example.com"

    with pytest.raises(BrowserAccountAuthorizationError):
        await service.execute_assigned_run_task(
            user_id=TEST_USER_ID,
            agent_id=TEST_AGENT_ID,
            account_id=account.id,
            session_id=TEST_CHAT_ID,
            run_id="automatic-run",
            instruction="Sign in to Eon and get my latest bill total",
        )
    assert len(provider.tasks) == 1

    async with sqlite_session_factory() as session:
        session.add_all(
            [
                AgentRunRecord(
                    id="missing-turn-run",
                    user_id=TEST_USER_ID,
                    agent_id=TEST_AGENT_ID,
                    session_id=TEST_CHAT_ID,
                    chat_id=TEST_CHAT_ID,
                    payload_json={},
                    created_by_user_id=TEST_USER_ID,
                ),
            ],
        )
        await session.commit()
    with pytest.raises(
        BrowserAccountAuthorizationError,
        match="The Browser Account request is invalid",
    ):
        await service.execute_assigned_run_task(
            user_id=TEST_USER_ID,
            agent_id=TEST_AGENT_ID,
            account_id=account.id,
            session_id=TEST_CHAT_ID,
            run_id="missing-turn-run",
            instruction="Read the bill",
        )
    assert len(provider.tasks) == 1

    voice_result = await service.execute_assigned_task(
        user_id=TEST_USER_ID,
        agent_id=TEST_AGENT_ID,
        account_id=account.id,
        session_id=TEST_CHAT_ID,
        client_turn_id="voice-turn",
        instruction="Read my current balance",
    )
    assert voice_result.successful is True
    assert provider.tasks[-1][0] == "Read my current balance"


@pytest.mark.asyncio
async def test_retrieval_cancellation_finishes_cleanup_before_propagating(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_agent_and_chat(sqlite_session_factory)
    provider = CancellationBlockingBrowserlessProvider()
    service = BrowserAccountService(
        session_factory=sqlite_session_factory,
        provider=provider,
        credential_resolver=FakeBrowserlessCredentialResolver(),
        secret_store=BrowserAccountSecretStore(user_secret_vault),
        settings=BrowserAccountSettings(enabled=True),
    )
    account = await service.create_account(
        user_id=TEST_USER_ID,
        request=BrowserAccountCreateRequest(
            label="Cancellable billing",
            primary_origin="https://billing.example.com",
            provider_connection_id="browserless-connection",
            agent_ids=(TEST_AGENT_ID,),
            username=SecretStr("billing@example.com"),
            password=SecretStr("website-password"),
        ),
    )
    await seed_chat_run(
        sqlite_session_factory,
        run_id="cancelled-browser-run",
        client_turn_id="cancelled-browser-turn",
    )
    execution = asyncio.create_task(
        service.execute_assigned_run_task(
            user_id=TEST_USER_ID,
            agent_id=TEST_AGENT_ID,
            account_id=account.id,
            session_id=TEST_CHAT_ID,
            run_id="cancelled-browser-run",
            instruction="Read my current balance",
        ),
    )
    await asyncio.wait_for(provider.wait_started.wait(), timeout=2)

    execution.cancel()
    await asyncio.wait_for(provider.stop_started.wait(), timeout=2)
    execution.cancel()
    provider.allow_stop.set()

    with pytest.raises(asyncio.CancelledError):
        await execution

    async with sqlite_session_factory() as session:
        lease = (await session.exec(select(BrowserAccountLeaseRecord))).one()
        authorization = (
            await session.exec(
                select(BrowserAccountRunAuthorizationRecord).where(
                    BrowserAccountRunAuthorizationRecord.run_id == "cancelled-browser-run",
                ),
            )
        ).one()
        session_handle = await service.secret_store.load_session_handle(
            session=session,
            user_id=TEST_USER_ID,
            credential_id=authorization.id,
        )
        audit_events = tuple(
            (
                await session.exec(
                    select(BrowserAccountAuditEventRecord).where(
                        BrowserAccountAuditEventRecord.run_id == "cancelled-browser-run",
                    ),
                )
            ).all(),
        )
    assert lease.active is False
    assert session_handle is None
    assert provider.stopped_sessions == 1
    assert [event.event_kind for event in audit_events][-1] == (BrowserAccountAuditEventKind.SESSION_STOPPED.value)

    await seed_chat_run(
        sqlite_session_factory,
        run_id="retry-browser-run",
        client_turn_id="retry-browser-turn",
    )
    result = await service.execute_assigned_run_task(
        user_id=TEST_USER_ID,
        agent_id=TEST_AGENT_ID,
        account_id=account.id,
        session_id=TEST_CHAT_ID,
        run_id="retry-browser-run",
        instruction="Read my current balance",
    )

    assert result.successful is True
    assert provider.stopped_sessions == 2


@pytest.mark.asyncio
async def test_assigned_browser_account_still_requires_agent_assignment(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_agent_and_chat(sqlite_session_factory)
    service = BrowserAccountService(
        session_factory=sqlite_session_factory,
        provider=FakeBrowserlessProvider(),
        credential_resolver=FakeBrowserlessCredentialResolver(),
        secret_store=BrowserAccountSecretStore(user_secret_vault),
        settings=BrowserAccountSettings(enabled=True),
    )
    account = await service.create_account(
        user_id=TEST_USER_ID,
        request=BrowserAccountCreateRequest(
            label="Unassigned billing",
            primary_origin="https://billing.example.com",
            provider_connection_id="browserless-connection",
            agent_ids=(),
            username=SecretStr("billing@example.com"),
            password=SecretStr("website-password"),
        ),
    )
    assert (
        await service.list_assigned_ready_accounts(
            user_id=TEST_USER_ID,
            agent_id=TEST_AGENT_ID,
        )
        == ()
    )
    with pytest.raises(BrowserAccountAuthorizationError):
        await service.execute_assigned_task(
            user_id=TEST_USER_ID,
            agent_id=TEST_AGENT_ID,
            account_id=account.id,
            session_id=TEST_CHAT_ID,
            client_turn_id="voice-turn",
            instruction="Read my bill",
        )


@pytest.mark.asyncio
async def test_scheduled_run_uses_its_bound_browserless_account_without_chat_approval(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_agent_and_chat(sqlite_session_factory)
    provider = FakeBrowserlessProvider()
    service = BrowserAccountService(
        session_factory=sqlite_session_factory,
        provider=provider,
        credential_resolver=FakeBrowserlessCredentialResolver(),
        secret_store=BrowserAccountSecretStore(user_secret_vault),
        settings=BrowserAccountSettings(enabled=True),
    )
    account = await service.create_account(
        user_id=TEST_USER_ID,
        request=BrowserAccountCreateRequest(
            label="Scheduled billing",
            primary_origin="https://billing.example.com",
            provider_connection_id="browserless-connection",
            agent_ids=(TEST_AGENT_ID,),
            username=SecretStr("scheduled@example.com"),
            password=SecretStr("scheduled-password"),
        ),
    )
    replacement_account = await service.create_account(
        user_id=TEST_USER_ID,
        request=BrowserAccountCreateRequest(
            label="Replacement scheduled billing",
            primary_origin="https://replacement.example.com",
            provider_connection_id="replacement-browserless-connection",
            agent_ids=(TEST_AGENT_ID,),
            username=SecretStr("replacement@example.com"),
            password=SecretStr("replacement-password"),
        ),
    )
    async with sqlite_session_factory() as session:
        session.add(
            AgentScheduleRecord(
                id="browser-schedule",
                user_id=TEST_USER_ID,
                agent_id=TEST_AGENT_ID,
                browser_account_id=account.id,
                name="Check bill",
                payload_json={"text": "Read the current bill total"},
            ),
        )
        session.add(
            AgentRunRecord(
                id="scheduled-browser-run",
                user_id=TEST_USER_ID,
                agent_id=TEST_AGENT_ID,
                schedule_id="browser-schedule",
                browser_account_id=account.id,
                payload_json={"text": "Read the current bill total"},
                created_by_user_id=TEST_USER_ID,
            ),
        )
        await session.commit()
    async with sqlite_session_factory() as session:
        schedule = await session.get(AgentScheduleRecord, "browser-schedule")
        assert schedule is not None
        schedule.browser_account_id = replacement_account.id
        await session.commit()

    pending = await service.pending_authorization(
        user_id=TEST_USER_ID,
        agent_id=TEST_AGENT_ID,
        session_id=TEST_CHAT_ID,
        run_id="scheduled-browser-run",
    )
    assert pending is not None
    assert pending.authorization_id is None
    assert pending.provider_connection_id == "browserless-connection"
    assert pending.execution_tool_name == "browserless_agent"
    assert pending.required is True

    result = await service.execute_authorized_task(
        user_id=TEST_USER_ID,
        agent_id=TEST_AGENT_ID,
        run_id="scheduled-browser-run",
        session_id=TEST_CHAT_ID,
    )

    assert result.successful is True
    assert provider.tasks == [
        (
            "Read the current bill total",
            "https://billing.example.com",
            ("https://billing.example.com",),
            40,
        ),
    ]
    with pytest.raises(BrowserAccountAuthorizationError):
        await service.execute_authorized_task(
            user_id=TEST_USER_ID,
            agent_id=TEST_AGENT_ID,
            run_id="scheduled-browser-run",
            session_id=TEST_CHAT_ID,
        )
    assert len(provider.tasks) == 1


@pytest.mark.asyncio
async def test_chat_approval_is_bound_only_to_its_client_turn_run(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_agent_and_chat(sqlite_session_factory)
    service = BrowserAccountService(
        session_factory=sqlite_session_factory,
        provider=FakeBrowserlessProvider(),
        credential_resolver=FakeBrowserlessCredentialResolver(),
        secret_store=BrowserAccountSecretStore(user_secret_vault),
        settings=BrowserAccountSettings(enabled=True),
    )
    account = await service.create_account(
        user_id=TEST_USER_ID,
        request=BrowserAccountCreateRequest(
            label="Turn-bound billing",
            primary_origin="https://billing.example.com",
            provider_connection_id="browserless-connection",
            agent_ids=(TEST_AGENT_ID,),
            username=SecretStr("turn@example.com"),
            password=SecretStr("turn-password"),
        ),
    )
    await seed_chat_run(
        sqlite_session_factory,
        run_id="approved-run",
        client_turn_id="approved-turn",
    )
    await seed_chat_run(
        sqlite_session_factory,
        run_id="later-run",
        client_turn_id="later-turn",
    )
    authorization = await service.approve_for_chat(
        user_id=TEST_USER_ID,
        request=BrowserAccountApprovalRequest(
            account_id=account.id,
            chat_id=TEST_CHAT_ID,
            client_turn_id="approved-turn",
            task_summary="Read the approved bill",
        ),
    )

    assert (
        await service.pending_authorization(
            user_id=TEST_USER_ID,
            agent_id=TEST_AGENT_ID,
            session_id=TEST_CHAT_ID,
            run_id="later-run",
        )
        is None
    )
    pending = await service.pending_authorization(
        user_id=TEST_USER_ID,
        agent_id=TEST_AGENT_ID,
        session_id=TEST_CHAT_ID,
        run_id="approved-run",
    )
    assert pending is not None
    assert pending.authorization_id == authorization.id


@pytest.mark.asyncio
async def test_browserless_revocation_waits_for_executor_owned_session_cleanup(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_agent_and_chat(sqlite_session_factory)
    executor_provider = BlockingBrowserlessProvider()
    executor_service = BrowserAccountService(
        session_factory=sqlite_session_factory,
        provider=executor_provider,
        credential_resolver=FakeBrowserlessCredentialResolver(),
        secret_store=BrowserAccountSecretStore(user_secret_vault),
        settings=BrowserAccountSettings(enabled=True, poll_interval_seconds=0.25),
    )
    revoker_provider = FakeBrowserlessProvider()
    revoker_service = BrowserAccountService(
        session_factory=sqlite_session_factory,
        provider=revoker_provider,
        credential_resolver=FakeBrowserlessCredentialResolver(),
        secret_store=BrowserAccountSecretStore(user_secret_vault),
        settings=BrowserAccountSettings(enabled=True, poll_interval_seconds=0.25),
    )
    account = await executor_service.create_account(
        user_id=TEST_USER_ID,
        request=BrowserAccountCreateRequest(
            label="Revocable billing",
            primary_origin="https://billing.example.com",
            provider_connection_id="browserless-connection",
            agent_ids=(TEST_AGENT_ID,),
            username=SecretStr("revoke@example.com"),
            password=SecretStr("revoke-password"),
        ),
    )
    await seed_chat_run(
        sqlite_session_factory,
        run_id="revoked-run",
        client_turn_id="revoked-turn",
    )
    authorization = await executor_service.approve_for_chat(
        user_id=TEST_USER_ID,
        request=BrowserAccountApprovalRequest(
            account_id=account.id,
            chat_id=TEST_CHAT_ID,
            client_turn_id="revoked-turn",
            task_summary="Read the bill",
        ),
    )
    execution = asyncio.create_task(
        executor_service.execute_authorized_task(
            user_id=TEST_USER_ID,
            agent_id=TEST_AGENT_ID,
            run_id="revoked-run",
            session_id=TEST_CHAT_ID,
            expected_authorization_id=authorization.id,
        ),
    )
    await asyncio.wait_for(executor_provider.started.wait(), timeout=2)

    revoked = await revoker_service.revoke_account(
        user_id=TEST_USER_ID,
        account_id=account.id,
    )

    assert revoked is not None
    assert revoked.status == BrowserAccountStatus.REVOKED
    with pytest.raises(BrowserAccountProviderError):
        await execution
    assert executor_provider.stopped_sessions == 1
    assert revoker_provider.stopped_sessions == 0
    async with sqlite_session_factory() as session:
        lease = (await session.exec(select(BrowserAccountLeaseRecord))).one()
        assert lease.active is False
        assert (
            await executor_service.secret_store.load_account_credentials(
                session=session,
                user_id=TEST_USER_ID,
                browser_account_id=account.id,
            )
            is None
        )


@pytest.mark.asyncio
async def test_expired_browserless_lease_is_reconciled_before_reuse(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_agent_and_chat(sqlite_session_factory)
    provider = FakeBrowserlessProvider()
    service = BrowserAccountService(
        session_factory=sqlite_session_factory,
        provider=provider,
        credential_resolver=FakeBrowserlessCredentialResolver(),
        secret_store=BrowserAccountSecretStore(user_secret_vault),
        settings=BrowserAccountSettings(enabled=True),
    )
    account = await service.create_account(
        user_id=TEST_USER_ID,
        request=BrowserAccountCreateRequest(
            label="Lease billing",
            primary_origin="https://billing.example.com",
            provider_connection_id="browserless-connection",
            agent_ids=(TEST_AGENT_ID,),
            username=SecretStr("lease@example.com"),
            password=SecretStr("lease-password"),
        ),
    )
    async with sqlite_session_factory() as session:
        session.add(
            AgentScheduleRecord(
                id="lease-schedule",
                user_id=TEST_USER_ID,
                agent_id=TEST_AGENT_ID,
                browser_account_id=account.id,
                name="Lease check",
                payload_json={"text": "Read the bill"},
            ),
        )
        session.add(
            AgentRunRecord(
                id="lease-run",
                user_id=TEST_USER_ID,
                agent_id=TEST_AGENT_ID,
                schedule_id="lease-schedule",
                browser_account_id=account.id,
                payload_json={"text": "Read the bill"},
                created_by_user_id=TEST_USER_ID,
            ),
        )
        session.add(
            BrowserAccountLeaseRecord(
                user_id=TEST_USER_ID,
                browser_account_id=account.id,
                owner_id="stale-run",
                lease_kind="task",
                expires_at=utc_now() - timedelta(seconds=1),
                heartbeat_at=utc_now() - timedelta(minutes=10),
                active=True,
            ),
        )
        await service.secret_store.seal_session_handle(
            session=session,
            user_id=TEST_USER_ID,
            credential_id="stale-run",
            handle=OpaqueBrowserSessionHandle(SecretStr("stale-session")),
            provider=BrowserAccountProvider.BROWSERLESS,
        )
        await session.commit()

    result = await service.execute_authorized_task(
        user_id=TEST_USER_ID,
        agent_id=TEST_AGENT_ID,
        run_id="lease-run",
        session_id=TEST_CHAT_ID,
    )

    assert result.successful is True
    async with sqlite_session_factory() as session:
        assert (
            await service.secret_store.load_session_handle(
                session=session,
                user_id=TEST_USER_ID,
                credential_id="stale-run",
            )
            is None
        )
        lease = (await session.exec(select(BrowserAccountLeaseRecord))).one()
        assert lease.owner_id == "lease-run"
        assert lease.active is False


@pytest.mark.asyncio
async def test_deleted_browser_account_label_can_be_reused(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_agent_and_chat(sqlite_session_factory)
    service = BrowserAccountService(
        session_factory=sqlite_session_factory,
        provider=FakeBrowserlessProvider(),
        credential_resolver=FakeBrowserlessCredentialResolver(),
        secret_store=BrowserAccountSecretStore(user_secret_vault),
        settings=BrowserAccountSettings(enabled=True),
    )
    request = BrowserAccountCreateRequest(
        label="Reusable billing",
        primary_origin="https://billing.example.com",
        provider_connection_id="browserless-connection",
        agent_ids=(TEST_AGENT_ID,),
        username=SecretStr("reuse@example.com"),
        password=SecretStr("reuse-password"),
    )
    deleted = await service.create_account(user_id=TEST_USER_ID, request=request)
    assert (
        await service.revoke_account(
            user_id=TEST_USER_ID,
            account_id=deleted.id,
            delete_after=True,
        )
        is None
    )

    recreated = await service.create_account(user_id=TEST_USER_ID, request=request)

    assert recreated.id != deleted.id


@pytest.mark.asyncio
async def test_empty_provider_result_cannot_complete_successfully(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_agent_and_chat(sqlite_session_factory)
    provider = FakeBrowserProvider(
        task_result=BrowserTaskResult(
            output_text="   ",
            successful=True,
        ),
    )
    service = browser_account_service(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        provider=provider,
    )
    account = await create_ready_account(service)
    await seed_chat_run(
        sqlite_session_factory,
        run_id="empty-result-run",
        client_turn_id="empty-result-turn",
    )
    authorization = await service.approve_for_chat(
        user_id=TEST_USER_ID,
        request=BrowserAccountApprovalRequest(
            account_id=account.id,
            chat_id=TEST_CHAT_ID,
            client_turn_id="empty-result-turn",
            task_summary="Read account data",
        ),
    )

    result = await service.execute_authorized_task(
        user_id=TEST_USER_ID,
        agent_id=TEST_AGENT_ID,
        run_id="empty-result-run",
        session_id=TEST_CHAT_ID,
        expected_authorization_id=authorization.id,
    )

    assert result == BrowserTaskResult(
        output_text=UNUSABLE_BROWSER_TASK_RESULT,
        successful=False,
    )


@pytest.mark.asyncio
async def test_profile_lifecycle_and_single_use_approval_execute_exact_user_task(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_agent_and_chat(sqlite_session_factory)
    provider = FakeBrowserProvider()
    service = BrowserAccountService(
        session_factory=sqlite_session_factory,
        provider=provider,
        credential_resolver=FakeCredentialResolver(),
        secret_store=BrowserAccountSecretStore(user_secret_vault),
        settings=BrowserAccountSettings(enabled=True),
    )

    account = await service.create_account(
        user_id=TEST_USER_ID,
        request=BrowserAccountCreateRequest(
            label="Billing",
            primary_origin="https://billing.example.com",
            authentication_origins=("https://login.example.com",),
            provider_connection_id="browser-use-connection",
            agent_ids=(TEST_AGENT_ID,),
        ),
    )
    setup = await service.start_setup(user_id=TEST_USER_ID, account_id=account.id)
    assert setup.live_url == "https://live.browser-use.com/short-lived-token"
    assert provider.setup_country_codes == ["GB"]
    ready = await service.complete_setup(user_id=TEST_USER_ID, account_id=account.id)
    assert ready.status == BrowserAccountStatus.READY

    await seed_chat_run(
        sqlite_session_factory,
        run_id="run-1",
        client_turn_id="billing-turn-1",
    )
    authorization = await service.approve_for_chat(
        user_id=TEST_USER_ID,
        request=BrowserAccountApprovalRequest(
            account_id=account.id,
            chat_id=TEST_CHAT_ID,
            client_turn_id="billing-turn-1",
            task_summary="Read the latest invoice total",
        ),
    )
    result = await service.execute_authorized_task(
        user_id=TEST_USER_ID,
        agent_id=TEST_AGENT_ID,
        run_id="run-1",
        session_id=TEST_CHAT_ID,
        expected_authorization_id=authorization.id,
    )

    assert result.output_text == "Invoice total: £42"
    assert provider.tasks == [
        (
            "Read the latest invoice total",
            "https://billing.example.com",
            ("https://billing.example.com", "https://login.example.com"),
            40,
        ),
    ]
    assert provider.task_country_codes == ["GB"]
    assert provider.stopped_sessions == 2
    await seed_chat_run(
        sqlite_session_factory,
        run_id="run-2",
        client_turn_id="billing-turn-2",
    )
    with pytest.raises(BrowserAccountAuthorizationError):
        await service.execute_authorized_task(
            user_id=TEST_USER_ID,
            agent_id=TEST_AGENT_ID,
            run_id="run-2",
            session_id=TEST_CHAT_ID,
            expected_authorization_id=authorization.id,
        )

    async with sqlite_session_factory() as session:
        secret_records = tuple((await session.exec(select(UserSecretRecord))).all())
        audit_records = tuple(
            (await session.exec(select(BrowserAccountAuditEventRecord))).all(),
        )
    assert secret_records
    assert all(b"opaque-profile-handle" not in record.ciphertext for record in secret_records)
    assert all("short-lived-token" not in repr(record) for record in audit_records)


@pytest.mark.asyncio
async def test_provider_timeout_is_actionable_and_clears_after_success(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_agent_and_chat(sqlite_session_factory)
    provider = FakeBrowserProvider(
        task_error_category=BrowserAccountErrorCategory.SESSION_TIMEOUT,
    )
    service = browser_account_service(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        provider=provider,
    )
    account = await create_ready_account(service)
    await seed_chat_run(
        sqlite_session_factory,
        run_id="timed-out-browser-run",
        client_turn_id="timed-out-browser-turn",
    )

    with pytest.raises(BrowserAccountProviderError) as error:
        await service.execute_assigned_run_task(
            user_id=TEST_USER_ID,
            agent_id=TEST_AGENT_ID,
            account_id=account.id,
            session_id=TEST_CHAT_ID,
            run_id="timed-out-browser-run",
            instruction="Read my current balance",
        )

    assert str(error.value) == ("Browser task timed out before the provider returned a result")
    failed_account = (await service.list_accounts(user_id=TEST_USER_ID))[0]
    assert failed_account.error_category == BrowserAccountErrorCategory.SESSION_TIMEOUT

    provider.task_error_category = None
    await seed_chat_run(
        sqlite_session_factory,
        run_id="recovered-browser-run",
        client_turn_id="recovered-browser-turn",
    )
    result = await service.execute_assigned_run_task(
        user_id=TEST_USER_ID,
        agent_id=TEST_AGENT_ID,
        account_id=account.id,
        session_id=TEST_CHAT_ID,
        run_id="recovered-browser-run",
        instruction="Read my current balance",
    )

    assert result.successful is True
    recovered_account = (await service.list_accounts(user_id=TEST_USER_ID))[0]
    assert recovered_account.error_category is None


@pytest.mark.asyncio
async def test_provider_start_failure_does_not_record_started_session(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_agent_and_chat(sqlite_session_factory)
    provider = StartFailingBrowserlessProvider()
    service = BrowserAccountService(
        session_factory=sqlite_session_factory,
        provider=provider,
        credential_resolver=FakeBrowserlessCredentialResolver(),
        secret_store=BrowserAccountSecretStore(user_secret_vault),
        settings=BrowserAccountSettings(enabled=True),
    )
    account = await service.create_account(
        user_id=TEST_USER_ID,
        request=BrowserAccountCreateRequest(
            label="Browserless billing",
            primary_origin="https://billing.example.com",
            provider_connection_id="browserless-connection",
            agent_ids=(TEST_AGENT_ID,),
            username=SecretStr("billing@example.com"),
            password=SecretStr("password-123"),
        ),
    )
    run_id = "browserless-start-failure-run"
    await seed_chat_run(
        sqlite_session_factory,
        run_id=run_id,
        client_turn_id="browserless-start-failure-turn",
    )

    with pytest.raises(BrowserAccountProviderError) as error:
        await service.execute_assigned_run_task(
            user_id=TEST_USER_ID,
            agent_id=TEST_AGENT_ID,
            account_id=account.id,
            session_id=TEST_CHAT_ID,
            run_id=run_id,
            instruction="Read my current balance",
        )

    assert error.value.category == BrowserAccountErrorCategory.PROVIDER_REQUEST_INVALID
    failed_account = (await service.list_accounts(user_id=TEST_USER_ID))[0]
    assert failed_account.error_category == BrowserAccountErrorCategory.PROVIDER_REQUEST_INVALID
    async with sqlite_session_factory() as session:
        audit_events = tuple(
            (
                await session.exec(
                    select(BrowserAccountAuditEventRecord).where(
                        BrowserAccountAuditEventRecord.run_id == run_id,
                    ),
                )
            ).all(),
        )
    assert [event.event_kind for event in audit_events] == [
        BrowserAccountAuditEventKind.APPROVAL_CREATED.value,
        BrowserAccountAuditEventKind.AUTHORITY_UNSEALED.value,
        BrowserAccountAuditEventKind.PROVIDER_ERROR.value,
    ]


@pytest.mark.asyncio
async def test_unsuccessful_provider_result_records_rejected_category(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_agent_and_chat(sqlite_session_factory)
    provider = FakeBrowserProvider(
        task_result=BrowserTaskResult(
            output_text="Login was rejected",
            successful=False,
        ),
    )
    service = browser_account_service(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        provider=provider,
    )
    account = await create_ready_account(service)
    await seed_chat_run(
        sqlite_session_factory,
        run_id="rejected-browser-run",
        client_turn_id="rejected-browser-turn",
    )

    result = await service.execute_assigned_run_task(
        user_id=TEST_USER_ID,
        agent_id=TEST_AGENT_ID,
        account_id=account.id,
        session_id=TEST_CHAT_ID,
        run_id="rejected-browser-run",
        instruction="Read my current balance",
    )

    assert result.successful is False
    failed_account = (await service.list_accounts(user_id=TEST_USER_ID))[0]
    assert failed_account.error_category == BrowserAccountErrorCategory.PROVIDER_REJECTED
    async with sqlite_session_factory() as session:
        audit_events = tuple(
            (
                await session.exec(
                    select(BrowserAccountAuditEventRecord).where(
                        BrowserAccountAuditEventRecord.browser_account_id == account.id,
                        BrowserAccountAuditEventRecord.event_kind == BrowserAccountAuditEventKind.PROVIDER_ERROR.value,
                    ),
                )
            ).all(),
        )
    assert len(audit_events) == 1
    assert audit_events[0].error_category == BrowserAccountErrorCategory.PROVIDER_REJECTED


@pytest.mark.asyncio
async def test_wrong_user_and_account_context_cannot_unseal_profile_handle(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    store = BrowserAccountSecretStore(user_secret_vault)
    async with sqlite_session_factory() as session:
        await store.seal_profile_handle(
            session=session,
            user_id="user-a",
            browser_account_id="account-a",
            handle=OpaqueBrowserProfileHandle(SecretStr("opaque-profile")),
        )
        await session.commit()
        assert (
            await store.load_profile_handle(
                session=session,
                user_id="user-a",
                browser_account_id="account-a",
            )
        ) is not None
        assert (
            await store.load_profile_handle(
                session=session,
                user_id="user-b",
                browser_account_id="account-a",
            )
        ) is None
        assert (
            await store.load_profile_handle(
                session=session,
                user_id="user-a",
                browser_account_id="account-b",
            )
        ) is None


@pytest.mark.asyncio
async def test_delete_intent_survives_remote_failure_and_reconciliation(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_agent_and_chat(sqlite_session_factory)
    provider = FakeBrowserProvider()
    service = browser_account_service(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        provider=provider,
    )
    account = await create_ready_account(service)

    provider.fail_profile_deletion = True
    with pytest.raises(BrowserAccountProviderError):
        await service.revoke_account(
            user_id=TEST_USER_ID,
            account_id=account.id,
            delete_after=True,
        )

    async with sqlite_session_factory() as session:
        record = await session.get(BrowserAccountRecord, account.id)
        assert record is not None
        assert record.status == BrowserAccountStatus.DELETION_PENDING.value
        assert record.delete_requested_at is not None
        assert record.deleted_at is None

    provider.fail_profile_deletion = False
    assert await service.list_accounts(user_id=TEST_USER_ID) == ()
    assert (
        await service.revoke_account(
            user_id=TEST_USER_ID,
            account_id=account.id,
            delete_after=True,
        )
        is None
    )
    assert provider.deleted_profiles == 1

    async with sqlite_session_factory() as session:
        record = await session.get(BrowserAccountRecord, account.id)
        assert record is not None
        assert record.deleted_at is not None
        assert (
            await service.secret_store.load_profile_handle(
                session=session,
                user_id=TEST_USER_ID,
                browser_account_id=account.id,
            )
            is None
        )


@pytest.mark.asyncio
async def test_revoke_stops_the_active_task_session_before_deleting_profile(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_agent_and_chat(sqlite_session_factory)
    provider = FakeBrowserProvider()
    service = browser_account_service(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        provider=provider,
    )
    account = await create_ready_account(service)
    await seed_chat_run(
        sqlite_session_factory,
        run_id="run-active",
        client_turn_id="active-turn",
    )
    authorization = await service.approve_for_chat(
        user_id=TEST_USER_ID,
        request=BrowserAccountApprovalRequest(
            account_id=account.id,
            chat_id=TEST_CHAT_ID,
            client_turn_id="active-turn",
            task_summary="Read account data",
        ),
    )
    consumed = await service.consume_authorization(
        user_id=TEST_USER_ID,
        agent_id=TEST_AGENT_ID,
        run_id="run-active",
        session_id=TEST_CHAT_ID,
        expected_authorization_id=authorization.id,
    )
    async with sqlite_session_factory() as session:
        await service.secret_store.seal_session_handle(
            session=session,
            user_id=TEST_USER_ID,
            credential_id=consumed.authorization_id,
            handle=OpaqueBrowserSessionHandle(SecretStr("active-task-session")),
        )
        await session.commit()

    revoked = await service.revoke_account(
        user_id=TEST_USER_ID,
        account_id=account.id,
    )

    assert revoked is not None
    assert revoked.status == BrowserAccountStatus.REVOKED
    assert provider.stopped_sessions == 2
    assert provider.deleted_profiles == 1
    async with sqlite_session_factory() as session:
        assert (
            await service.secret_store.load_session_handle(
                session=session,
                user_id=TEST_USER_ID,
                credential_id=consumed.authorization_id,
            )
            is None
        )
        lease_result = await session.exec(select(BrowserAccountLeaseRecord))
        lease = lease_result.one()
        assert lease.active is False


def browser_account_service(
    *,
    session_factory: AsyncSessionFactory,
    secret_vault: UserSecretVaultService,
    provider: FakeBrowserProvider,
) -> BrowserAccountService:
    return BrowserAccountService(
        session_factory=session_factory,
        provider=provider,
        credential_resolver=FakeCredentialResolver(),
        secret_store=BrowserAccountSecretStore(secret_vault),
        settings=BrowserAccountSettings(enabled=True),
    )


async def create_ready_account(
    service: BrowserAccountService,
) -> BrowserAccountResponse:
    account = await service.create_account(
        user_id=TEST_USER_ID,
        request=BrowserAccountCreateRequest(
            label="Billing",
            primary_origin="https://billing.example.com",
            authentication_origins=("https://login.example.com",),
            provider_connection_id="browser-use-connection",
            agent_ids=(TEST_AGENT_ID,),
        ),
    )
    await service.start_setup(user_id=TEST_USER_ID, account_id=account.id)
    return await service.complete_setup(
        user_id=TEST_USER_ID,
        account_id=account.id,
    )


async def seed_agent_and_chat(session_factory: AsyncSessionFactory) -> None:
    async with session_factory() as session:
        session.add(
            UserAgentRecord(
                id=TEST_AGENT_ID,
                owner_user_id=TEST_USER_ID,
                slug="browser-agent",
                display_name="Browser Agent",
            ),
        )
        session.add(
            AgentSessionRecord(
                id=TEST_CHAT_ID,
                user_id=TEST_USER_ID,
                agent_id=TEST_AGENT_ID,
                provider="openai",
                model="gpt-5.4-mini",
                channel="text",
            ),
        )
        await session.commit()


async def seed_chat_run(
    session_factory: AsyncSessionFactory,
    *,
    run_id: str,
    client_turn_id: str,
    task: str = "Read the account data",
) -> None:
    async with session_factory() as session:
        session.add(
            AgentRunRecord(
                id=run_id,
                user_id=TEST_USER_ID,
                agent_id=TEST_AGENT_ID,
                session_id=TEST_CHAT_ID,
                chat_id=TEST_CHAT_ID,
                payload_json={"client_turn_id": client_turn_id},
                created_by_user_id=TEST_USER_ID,
            ),
        )
        session.add(
            AgentTranscriptRecord(
                session_id=TEST_CHAT_ID,
                user_id=TEST_USER_ID,
                agent_id=TEST_AGENT_ID,
                role=AgentTranscriptRole.USER.value,
                text=task,
                client_turn_id=client_turn_id,
                source=AgentTranscriptSource.TEXT_INPUT.value,
                is_final=True,
            ),
        )
        await session.commit()

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlmodel import col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_identity.records import UserAgentRecord
from voxos.agent_runtime.records import (
    AgentRunRecord,
    AgentSessionRecord,
)
from voxos.browser_accounts.audit import add_browser_account_audit_event
from voxos.browser_accounts.connection import BrowserProviderCredentialResolver
from voxos.browser_accounts.errors import (
    BrowserAccountAuthorizationError,
    BrowserAccountConflictError,
    BrowserAccountNotFoundError,
    BrowserAccountProviderConnectionError,
    BrowserAccountProviderError,
    BrowserAccountsDisabledError,
    BrowserAccountValidationError,
)
from voxos.browser_accounts.model_resolution import BrowserTaskModelCredentialResolver
from voxos.browser_accounts.models import (
    BrowserAccountAgentAssignmentsRequest,
    BrowserAccountApprovalRequest,
    BrowserAccountAuditEventKind,
    BrowserAccountAuthenticationMode,
    BrowserAccountAuthorizationResponse,
    BrowserAccountCreateRequest,
    BrowserAccountCredentialsRequest,
    BrowserAccountErrorCategory,
    BrowserAccountLeaseKind,
    BrowserAccountProvider,
    BrowserAccountResponse,
    BrowserAccountSetupResponse,
    BrowserAccountStatus,
    BrowserAccountUpdateRequest,
)
from voxos.browser_accounts.providers.base import (
    BrowserAccountProviderAdapter,
    BrowserProviderCredential,
    BrowserProviderOperationError,
    BrowserProviderTaskSession,
)
from voxos.browser_accounts.records import (
    AgentBrowserAccountGrantRecord,
    BrowserAccountLeaseRecord,
    BrowserAccountRecord,
    BrowserAccountRunAuthorizationRecord,
)
from voxos.browser_accounts.secrets import (
    BROWSER_ACCOUNT_CREDENTIAL_ENCRYPTION_PROVIDER,
    BrowserAccountSecretStore,
    BrowserWebsiteCredentials,
    OpaqueBrowserProfileHandle,
    OpaqueBrowserSessionHandle,
)
from voxos.browser_accounts.settings import BrowserAccountSettings
from voxos.browser_accounts.telemetry import record_remote_deletion_backlog
from voxos.browser_runtime.contracts import (
    BrowserTaskActionMode,
    BrowserTaskRequest,
    BrowserTaskResult,
    BrowserTaskVariable,
)
from voxos.browser_runtime.models import browser_url_origin, normalize_browser_start_url
from voxos.credentials.models import SecretPurpose
from voxos.credentials.records import UserSecretRecord
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory

logger = logging.getLogger(__name__)
ACCOUNT_LABEL_CONFLICT = "A Browser Account with that label already exists"
SETUP_ALREADY_ACTIVE = "Browser account setup is already active"
SETUP_NOT_ACTIVE = "Browser account setup is not active"
SETUP_NOT_RESUMABLE = "Browser account setup cannot be resumed"
ACCOUNT_NOT_READY = "Browser account is not ready"
ACCOUNT_REAUTHENTICATION_REQUIRED = "Browser account needs reauthentication"
ACCOUNT_ALREADY_IN_USE = "Browser account is already in use"
SCHEDULED_ACCOUNT_ALREADY_USED = "The scheduled Browser Account has already been used for this run attempt"
ASSIGNED_ACCOUNT_ALREADY_USED = "The Browser Account has already been used for this request"
ASSIGNED_ACCOUNT_TASK_INVALID = "The Browser Account request is invalid"
UNUSABLE_BROWSER_TASK_RESULT = "Browser task completed without a usable result."
AGENT_NOT_FOUND = "Agent was not found"
CHAT_NOT_FOUND = "Chat was not found"
AGENT_ACCOUNT_GRANT_REQUIRED = "Agent is not assigned to this Browser Account"
STORED_CREDENTIALS_ONLY = "Stored credentials are supported only for local and Browserless accounts"
STORED_CREDENTIALS_REQUIRED = "Local and Browserless accounts require a username and password"
STORED_CREDENTIALS_SETUP_UNAVAILABLE = "This account uses encrypted stored credentials"
BROWSER_COUNTRY_CHANGE_SETUP_ACTIVE = "Cancel the active browser setup before changing its country"
HOSTED_CONNECTION_REQUIRED = "Hosted browser accounts require a provider connection"
PROVIDER_CONNECTION_MISMATCH = "Browser provider does not match the selected connection"
SETUP_ALLOWED_STATUSES = frozenset(
    {
        BrowserAccountStatus.SETUP_REQUIRED,
        BrowserAccountStatus.READY,
        BrowserAccountStatus.EXPIRED,
        BrowserAccountStatus.ERROR,
    },
)


@dataclass(frozen=True, slots=True)
class ConsumedBrowserAuthorization:
    authorization_id: str
    audit_authorization_id: str | None
    browser_account_id: str
    provider_connection_id: str | None
    provider: BrowserAccountProvider
    profile_origins: tuple[str, ...]
    primary_origin: str
    start_url: str
    approved_task: str
    action_mode: BrowserTaskActionMode


@dataclass(frozen=True, slots=True)
class PendingBrowserAuthorization:
    authorization_id: str | None
    provider_connection_id: str | None
    execution_tool_name: str
    required: bool
    provider: BrowserAccountProvider = BrowserAccountProvider.BROWSER_USE_CLOUD


@dataclass(frozen=True, slots=True)
class AvailableBrowserAccount:
    account_id: str
    label: str
    provider: BrowserAccountProvider
    provider_connection_id: str | None
    primary_origin: str
    execution_tool_name: str


@dataclass(frozen=True, slots=True)
class BrowserAccountService:
    session_factory: AsyncSessionFactory
    provider: BrowserAccountProviderAdapter
    credential_resolver: BrowserProviderCredentialResolver
    secret_store: BrowserAccountSecretStore
    settings: BrowserAccountSettings
    model_credential_resolver: BrowserTaskModelCredentialResolver | None = None

    async def create_account(
        self,
        *,
        user_id: str,
        request: BrowserAccountCreateRequest,
    ) -> BrowserAccountResponse:
        self._require_enabled()
        async with self.session_factory() as session:
            credential = await self._resolve_requested_provider_credential(
                session=session,
                user_id=user_id,
                request=request,
            )
            await self._require_agents(
                session=session,
                user_id=user_id,
                agent_ids=request.agent_ids,
            )
            uses_stored_credentials = self._uses_stored_credentials(
                credential.provider,
            )
            if uses_stored_credentials and (request.username is None or request.password is None):
                raise BrowserAccountValidationError(
                    STORED_CREDENTIALS_REQUIRED,
                )
            if not uses_stored_credentials and (request.username is not None or request.password is not None):
                raise BrowserAccountValidationError(
                    STORED_CREDENTIALS_ONLY,
                )
            now = utc_now()
            account = BrowserAccountRecord(
                user_id=user_id,
                label=request.label,
                provider=credential.provider.value,
                provider_connection_id=request.provider_connection_id,
                primary_origin=request.primary_origin,
                authentication_origins=list(request.authentication_origins),
                browser_country_code=request.browser_country_code,
                authentication_mode=(
                    BrowserAccountAuthenticationMode.STORED_CREDENTIALS.value
                    if uses_stored_credentials
                    else BrowserAccountAuthenticationMode.MANAGED_PROFILE.value
                ),
                status=(
                    BrowserAccountStatus.READY.value
                    if uses_stored_credentials
                    else BrowserAccountStatus.SETUP_REQUIRED.value
                ),
                last_verified_at=now if uses_stored_credentials else None,
            )
            session.add(account)
            try:
                await session.flush()
                if uses_stored_credentials and request.username is not None and request.password is not None:
                    await self.secret_store.seal_account_credentials(
                        session=session,
                        user_id=user_id,
                        browser_account_id=account.id,
                        credentials=BrowserWebsiteCredentials(
                            username=request.username,
                            password=request.password,
                        ),
                    )
                for agent_id in request.agent_ids:
                    session.add(
                        AgentBrowserAccountGrantRecord(
                            user_id=user_id,
                            agent_id=agent_id,
                            browser_account_id=account.id,
                        ),
                    )
                add_browser_account_audit_event(
                    session=session,
                    user_id=user_id,
                    browser_account_id=account.id,
                    event_kind=BrowserAccountAuditEventKind.ACCOUNT_CREATED,
                    outcome="succeeded",
                )
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise BrowserAccountConflictError(
                    ACCOUNT_LABEL_CONFLICT,
                ) from exc
            await session.refresh(account)
            return await self._response(session, account)

    async def update_credentials(
        self,
        *,
        user_id: str,
        account_id: str,
        request: BrowserAccountCredentialsRequest,
    ) -> BrowserAccountResponse:
        self._require_enabled()
        async with self.session_factory() as session:
            account = await self._require_account(session, user_id, account_id)
            if not self._uses_stored_credentials(
                BrowserAccountProvider(account.provider),
            ):
                raise BrowserAccountConflictError(
                    STORED_CREDENTIALS_ONLY,
                )
            await self.secret_store.seal_account_credentials(
                session=session,
                user_id=user_id,
                browser_account_id=account.id,
                credentials=BrowserWebsiteCredentials(
                    username=request.username,
                    password=request.password,
                ),
            )
            now = utc_now()
            account.status = BrowserAccountStatus.READY.value
            account.error_category = None
            account.last_verified_at = now
            account.updated_at = now
            account.version += 1
            await session.commit()
            await session.refresh(account)
            return await self._response(session, account)

    async def update_account(
        self,
        *,
        user_id: str,
        account_id: str,
        request: BrowserAccountUpdateRequest,
    ) -> BrowserAccountResponse:
        self._require_enabled()
        async with self.session_factory() as session:
            account = await self._require_account(session, user_id, account_id)
            authentication_origins = list(request.authentication_origins)
            browser_country_changed = account.browser_country_code != request.browser_country_code
            if (
                account.label == request.label
                and account.primary_origin == request.primary_origin
                and account.authentication_origins == authentication_origins
                and not browser_country_changed
            ):
                return await self._response(session, account)
            if (
                browser_country_changed
                and BrowserAccountStatus(account.status) == BrowserAccountStatus.SETUP_IN_PROGRESS
            ):
                raise BrowserAccountConflictError(
                    BROWSER_COUNTRY_CHANGE_SETUP_ACTIVE,
                )

            account.label = request.label
            account.primary_origin = request.primary_origin
            account.authentication_origins = authentication_origins
            account.browser_country_code = request.browser_country_code
            if (
                browser_country_changed
                and BrowserAccountProvider(account.provider) == BrowserAccountProvider.BROWSER_USE_CLOUD
            ):
                account.status = BrowserAccountStatus.SETUP_REQUIRED.value
                account.error_category = None
                account.last_verified_at = None
            account.updated_at = utc_now()
            account.version += 1
            add_browser_account_audit_event(
                session=session,
                user_id=user_id,
                browser_account_id=account.id,
                event_kind=BrowserAccountAuditEventKind.ACCOUNT_UPDATED,
                outcome="succeeded",
            )
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise BrowserAccountConflictError(
                    ACCOUNT_LABEL_CONFLICT,
                ) from exc
            await session.refresh(account)
            return await self._response(session, account)

    async def list_accounts(self, *, user_id: str) -> tuple[BrowserAccountResponse, ...]:
        self._require_enabled()
        await self.reconcile_user(user_id=user_id)
        async with self.session_factory() as session:
            statement = (
                select(BrowserAccountRecord)
                .where(
                    col(BrowserAccountRecord.user_id) == user_id,
                    col(BrowserAccountRecord.deleted_at).is_(None),
                )
                .order_by(col(BrowserAccountRecord.label))
            )
            result = await session.exec(statement)
            accounts = tuple(result.all())
            grants = await self._grants_for_accounts(
                session=session,
                user_id=user_id,
                account_ids=tuple(account.id for account in accounts),
            )
            credential_account_ids = await self._credential_account_ids(
                session=session,
                user_id=user_id,
                account_ids=tuple(account.id for account in accounts),
            )
            return tuple(
                self._response_from_record(
                    account,
                    grants.get(account.id, ()),
                    has_credentials=account.id in credential_account_ids,
                )
                for account in accounts
            )

    async def list_assigned_ready_accounts(
        self,
        *,
        user_id: str,
        agent_id: str,
    ) -> tuple[AvailableBrowserAccount, ...]:
        if not self.settings.enabled:
            return ()
        async with self.session_factory() as session:
            grant_statement = select(AgentBrowserAccountGrantRecord.browser_account_id).where(
                col(AgentBrowserAccountGrantRecord.user_id) == user_id,
                col(AgentBrowserAccountGrantRecord.agent_id) == agent_id,
            )
            grant_result = await session.exec(grant_statement)
            account_ids = tuple(grant_result.all())
            if not account_ids:
                return ()
            account_statement = (
                select(BrowserAccountRecord)
                .where(
                    col(BrowserAccountRecord.user_id) == user_id,
                    col(BrowserAccountRecord.id).in_(account_ids),
                    col(BrowserAccountRecord.status) == BrowserAccountStatus.READY.value,
                    col(BrowserAccountRecord.deleted_at).is_(None),
                )
                .order_by(col(BrowserAccountRecord.label), col(BrowserAccountRecord.id))
            )
            account_result = await session.exec(account_statement)
            available: list[AvailableBrowserAccount] = []
            for account in account_result.all():
                if BrowserAccountProvider(account.provider) != BrowserAccountProvider.STAGEHAND_LOCAL:
                    if account.provider_connection_id is None:
                        continue
                    try:
                        await self.credential_resolver.require_connection(
                            session=session,
                            user_id=user_id,
                            connection_id=account.provider_connection_id,
                        )
                    except BrowserAccountProviderConnectionError:
                        continue
                available.append(
                    AvailableBrowserAccount(
                        account_id=account.id,
                        label=account.label,
                        provider=BrowserAccountProvider(account.provider),
                        provider_connection_id=account.provider_connection_id,
                        primary_origin=account.primary_origin,
                        execution_tool_name=self._execution_tool_name(account),
                    ),
                )
            return tuple(available)

    async def set_agent_assignments(
        self,
        *,
        user_id: str,
        account_id: str,
        request: BrowserAccountAgentAssignmentsRequest,
    ) -> BrowserAccountResponse:
        self._require_enabled()
        async with self.session_factory() as session:
            account = await self._require_account(session, user_id, account_id)
            await self._require_agents(
                session=session,
                user_id=user_id,
                agent_ids=request.agent_ids,
            )
            await session.exec(
                delete(AgentBrowserAccountGrantRecord).where(
                    col(AgentBrowserAccountGrantRecord.user_id) == user_id,
                    col(AgentBrowserAccountGrantRecord.browser_account_id) == account_id,
                ),
            )
            for agent_id in request.agent_ids:
                session.add(
                    AgentBrowserAccountGrantRecord(
                        user_id=user_id,
                        agent_id=agent_id,
                        browser_account_id=account_id,
                    ),
                )
            account.updated_at = utc_now()
            account.version += 1
            await session.commit()
            await session.refresh(account)
            return await self._response(session, account)

    async def start_setup(
        self,
        *,
        user_id: str,
        account_id: str,
    ) -> BrowserAccountSetupResponse:
        self._require_enabled()
        async with self.session_factory() as session:
            account = await self._require_account(session, user_id, account_id)
            if self._uses_stored_credentials(
                BrowserAccountProvider(account.provider),
            ):
                raise BrowserAccountConflictError(
                    STORED_CREDENTIALS_SETUP_UNAVAILABLE,
                )
            status = BrowserAccountStatus(account.status)
            if status not in SETUP_ALLOWED_STATUSES:
                raise BrowserAccountConflictError(
                    SETUP_ALREADY_ACTIVE,
                )
            credential = await self._resolve_account_provider_credential(
                session=session,
                user_id=user_id,
                account=account,
            )
            await self._acquire_lease(
                session=session,
                account=account,
                owner_id=f"setup:{account.id}",
                kind=BrowserAccountLeaseKind.SETUP,
                duration_seconds=self.settings.setup_ttl_seconds,
            )
            await session.commit()

            profile = await self.secret_store.load_profile_handle(
                session=session,
                user_id=user_id,
                browser_account_id=account.id,
            )
            created_profile = profile is None
            setup_session: OpaqueBrowserSessionHandle | None = None
            try:
                if profile is None:
                    profile = await self.provider.create_profile(
                        credential=credential,
                        account_label=account.label,
                        external_user_reference=account.id,
                    )
                    await self.secret_store.seal_profile_handle(
                        session=session,
                        user_id=user_id,
                        browser_account_id=account.id,
                        handle=profile,
                    )
                    await session.commit()

                setup = await self.provider.start_setup_session(
                    credential=credential,
                    profile=profile,
                    browser_country_code=account.browser_country_code,
                )
                setup_session = setup.handle
                await self.secret_store.seal_session_handle(
                    session=session,
                    user_id=user_id,
                    credential_id=account.id,
                    handle=setup.handle,
                )
                account.status = BrowserAccountStatus.SETUP_IN_PROGRESS.value
                account.error_category = None
                account.setup_started_at = utc_now()
                account.updated_at = utc_now()
                account.version += 1
                add_browser_account_audit_event(
                    session=session,
                    user_id=user_id,
                    browser_account_id=account.id,
                    event_kind=BrowserAccountAuditEventKind.SETUP_STARTED,
                    outcome="succeeded",
                )
                await session.commit()
                await session.refresh(account)
                return BrowserAccountSetupResponse(
                    account=await self._response(session, account),
                    live_url=setup.live_url,
                    expires_at=setup.expires_at,
                )
            except (Exception, asyncio.CancelledError) as exc:
                if setup_session is not None:
                    try:
                        await self._stop_session(credential, setup_session)
                    except BrowserProviderOperationError:
                        logger.exception(
                            "browser account setup session cleanup failed",
                            extra={
                                "browser_account_id": account.id,
                                "provider": account.provider,
                                "origin": account.primary_origin,
                                "operation": "stop_failed_setup_session",
                            },
                        )
                category = (
                    exc.category
                    if isinstance(exc, BrowserProviderOperationError)
                    else BrowserAccountErrorCategory.UNKNOWN
                )
                await self._handle_setup_failure(
                    session=session,
                    account=account,
                    credential=credential,
                    profile=profile,
                    created_profile=created_profile,
                    category=category,
                )
                if isinstance(exc, BrowserProviderOperationError):
                    raise BrowserAccountProviderError(exc.category) from exc
                raise

    async def complete_setup(
        self,
        *,
        user_id: str,
        account_id: str,
    ) -> BrowserAccountResponse:
        self._require_enabled()
        async with self.session_factory() as session:
            account = await self._require_account(session, user_id, account_id)
            current_status = BrowserAccountStatus(account.status)
            if current_status == BrowserAccountStatus.READY:
                return await self._response(session, account)
            if current_status != BrowserAccountStatus.SETUP_IN_PROGRESS:
                raise BrowserAccountConflictError(SETUP_NOT_ACTIVE)
            credential = await self._resolve_account_provider_credential(
                session=session,
                user_id=user_id,
                account=account,
            )
            profile = await self.secret_store.load_profile_handle(
                session=session,
                user_id=user_id,
                browser_account_id=account.id,
            )
            setup_session = await self.secret_store.load_session_handle(
                session=session,
                user_id=user_id,
                credential_id=account.id,
            )
            if profile is None or setup_session is None:
                raise BrowserAccountConflictError(SETUP_NOT_RESUMABLE)
            add_browser_account_audit_event(
                session=session,
                user_id=user_id,
                browser_account_id=account.id,
                event_kind=BrowserAccountAuditEventKind.AUTHORITY_UNSEALED,
                outcome="succeeded",
            )
            try:
                await self._stop_session(credential, setup_session)
                profile_exists = await self.provider.profile_exists(
                    credential=credential,
                    profile=profile,
                )
            except BrowserProviderOperationError as exc:
                account.status = BrowserAccountStatus.ERROR.value
                account.error_category = exc.category.value
                add_browser_account_audit_event(
                    session=session,
                    user_id=user_id,
                    browser_account_id=account.id,
                    event_kind=BrowserAccountAuditEventKind.PROVIDER_ERROR,
                    outcome="failed",
                    error_category=exc.category,
                )
                await session.commit()
                raise BrowserAccountProviderError(exc.category) from exc

            if not profile_exists:
                account.status = BrowserAccountStatus.EXPIRED.value
                account.error_category = BrowserAccountErrorCategory.PROFILE_MISSING.value
                await self.secret_store.delete_session_handle(
                    session=session,
                    user_id=user_id,
                    credential_id=account.id,
                )
                await self._release_lease(session, account.id)
                add_browser_account_audit_event(
                    session=session,
                    user_id=user_id,
                    browser_account_id=account.id,
                    event_kind=BrowserAccountAuditEventKind.AUTHENTICATION_EXPIRED,
                    outcome="failed",
                    error_category=BrowserAccountErrorCategory.PROFILE_MISSING,
                )
                await session.commit()
                raise BrowserAccountProviderError(
                    BrowserAccountErrorCategory.PROFILE_MISSING,
                )

            await self.secret_store.delete_session_handle(
                session=session,
                user_id=user_id,
                credential_id=account.id,
            )
            await self._release_lease(session, account.id)
            now = utc_now()
            account.status = BrowserAccountStatus.READY.value
            account.error_category = None
            account.setup_started_at = None
            account.last_verified_at = now
            account.updated_at = now
            account.version += 1
            add_browser_account_audit_event(
                session=session,
                user_id=user_id,
                browser_account_id=account.id,
                event_kind=BrowserAccountAuditEventKind.SETUP_COMPLETED,
                outcome="succeeded",
            )
            await session.commit()
            await session.refresh(account)
            return await self._response(session, account)

    async def cancel_setup(
        self,
        *,
        user_id: str,
        account_id: str,
    ) -> BrowserAccountResponse:
        self._require_enabled()
        async with self.session_factory() as session:
            account = await self._require_account(session, user_id, account_id)
            if BrowserAccountStatus(account.status) != BrowserAccountStatus.SETUP_IN_PROGRESS:
                return await self._response(session, account)
            credential = await self._resolve_account_provider_credential(
                session=session,
                user_id=user_id,
                account=account,
                require_active=False,
            )
            await self._stop_stored_session(
                session=session,
                user_id=user_id,
                credential_id=account.id,
                credential=credential,
            )
            await self._release_lease(session, account.id)
            account.status = (
                BrowserAccountStatus.SETUP_REQUIRED.value
                if account.last_verified_at is None
                else BrowserAccountStatus.EXPIRED.value
            )
            account.setup_started_at = None
            account.updated_at = utc_now()
            account.version += 1
            add_browser_account_audit_event(
                session=session,
                user_id=user_id,
                browser_account_id=account.id,
                event_kind=BrowserAccountAuditEventKind.SETUP_CANCELLED,
                outcome="succeeded",
            )
            await session.commit()
            await session.refresh(account)
            return await self._response(session, account)

    async def approve_for_chat(
        self,
        *,
        user_id: str,
        request: BrowserAccountApprovalRequest,
    ) -> BrowserAccountAuthorizationResponse:
        self._require_enabled()
        async with self.session_factory() as session:
            account = await self._require_account(
                session,
                user_id,
                request.account_id,
            )
            chat: AgentSessionRecord | None = None
            try:
                if BrowserAccountStatus(account.status) != BrowserAccountStatus.READY:
                    raise BrowserAccountConflictError(ACCOUNT_NOT_READY)
                chat = await self._require_chat(
                    session,
                    user_id,
                    request.chat_id,
                    for_update=True,
                )
                if chat.agent_id is None:
                    raise BrowserAccountAuthorizationError
                await self._require_account_grant(
                    session=session,
                    user_id=user_id,
                    account_id=account.id,
                    agent_id=chat.agent_id,
                )
            except (
                BrowserAccountAuthorizationError,
                BrowserAccountConflictError,
            ):
                add_browser_account_audit_event(
                    session=session,
                    user_id=user_id,
                    browser_account_id=account.id,
                    event_kind=BrowserAccountAuditEventKind.APPROVAL_DENIED,
                    outcome="denied",
                    agent_id=None if chat is None else chat.agent_id,
                )
                await session.commit()
                raise
            if chat.agent_id is None:
                raise BrowserAccountAuthorizationError
            now = utc_now()
            existing_statement = select(
                BrowserAccountRunAuthorizationRecord,
            ).where(
                col(BrowserAccountRunAuthorizationRecord.user_id) == user_id,
                col(BrowserAccountRunAuthorizationRecord.agent_id) == chat.agent_id,
                col(BrowserAccountRunAuthorizationRecord.session_id) == chat.id,
                col(BrowserAccountRunAuthorizationRecord.consumed_at).is_(None),
                col(BrowserAccountRunAuthorizationRecord.revoked_at).is_(None),
            )
            existing_result = await session.exec(existing_statement)
            for existing in existing_result.all():
                existing.revoked_at = now

            approved_origins = [account.primary_origin, *account.authentication_origins]
            authorization = BrowserAccountRunAuthorizationRecord(
                user_id=user_id,
                browser_account_id=account.id,
                agent_id=chat.agent_id,
                session_id=chat.id,
                client_turn_id=request.client_turn_id,
                approved_origins=approved_origins,
                task_summary=request.task_summary,
                approved_by_user_id=user_id,
                approved_at=now,
                expires_at=now + timedelta(seconds=self.settings.authorization_ttl_seconds),
            )
            session.add(authorization)
            add_browser_account_audit_event(
                session=session,
                user_id=user_id,
                browser_account_id=account.id,
                event_kind=BrowserAccountAuditEventKind.APPROVAL_CREATED,
                outcome="succeeded",
                authorization_id=authorization.id,
                agent_id=chat.agent_id,
                session_id=chat.id,
            )
            await session.commit()
            await session.refresh(authorization)
            return self._authorization_response(authorization, account)

    async def pending_authorization(
        self,
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
        run_id: str | None = None,
    ) -> PendingBrowserAuthorization | None:
        if not self.settings.enabled:
            return None
        if run_id is None:
            return None
        now = utc_now()
        async with self.session_factory() as session:
            scheduled = await self._pending_scheduled_authorization(
                session=session,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
            )
            if scheduled is not None:
                return scheduled
            await self._bind_chat_authorization_to_run(
                session=session,
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                run_id=run_id,
                now=now,
            )
            statement = (
                select(BrowserAccountRunAuthorizationRecord)
                .where(
                    col(BrowserAccountRunAuthorizationRecord.user_id) == user_id,
                    col(BrowserAccountRunAuthorizationRecord.agent_id) == agent_id,
                    col(BrowserAccountRunAuthorizationRecord.session_id) == session_id,
                    col(BrowserAccountRunAuthorizationRecord.run_id) == run_id,
                    col(BrowserAccountRunAuthorizationRecord.expires_at) > now,
                    col(BrowserAccountRunAuthorizationRecord.consumed_at).is_(None),
                    col(BrowserAccountRunAuthorizationRecord.revoked_at).is_(None),
                )
                .order_by(col(BrowserAccountRunAuthorizationRecord.approved_at).desc())
            )
            result = await session.exec(statement)
            authorization = result.first()
            if authorization is None:
                return None
            account = await self._require_account(
                session,
                user_id,
                authorization.browser_account_id,
            )
            if BrowserAccountStatus(account.status) != BrowserAccountStatus.READY:
                return None
            try:
                await self._require_account_grant(
                    session=session,
                    user_id=user_id,
                    account_id=account.id,
                    agent_id=agent_id,
                )
            except BrowserAccountAuthorizationError:
                return None
            return PendingBrowserAuthorization(
                authorization_id=authorization.id,
                provider_connection_id=account.provider_connection_id,
                provider=BrowserAccountProvider(account.provider),
                execution_tool_name=self._execution_tool_name(account),
                required=False,
            )

    async def _bind_chat_authorization_to_run(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        agent_id: str,
        session_id: str,
        run_id: str,
        now: datetime,
    ) -> None:
        run_statement = select(AgentRunRecord).where(
            col(AgentRunRecord.id) == run_id,
            col(AgentRunRecord.user_id) == user_id,
            col(AgentRunRecord.agent_id) == agent_id,
            col(AgentRunRecord.session_id) == session_id,
        )
        run_result = await session.exec(run_statement)
        run = run_result.one_or_none()
        if run is None:
            return
        client_turn_id = run.payload_json.get("client_turn_id")
        if not isinstance(client_turn_id, str) or not client_turn_id:
            return
        statement = (
            select(BrowserAccountRunAuthorizationRecord)
            .where(
                col(BrowserAccountRunAuthorizationRecord.user_id) == user_id,
                col(BrowserAccountRunAuthorizationRecord.agent_id) == agent_id,
                col(BrowserAccountRunAuthorizationRecord.session_id) == session_id,
                col(BrowserAccountRunAuthorizationRecord.client_turn_id) == client_turn_id,
                col(BrowserAccountRunAuthorizationRecord.run_id).is_(None),
                col(BrowserAccountRunAuthorizationRecord.expires_at) > now,
                col(BrowserAccountRunAuthorizationRecord.consumed_at).is_(None),
                col(BrowserAccountRunAuthorizationRecord.revoked_at).is_(None),
            )
            .with_for_update()
        )
        result = await session.exec(statement)
        authorization = result.one_or_none()
        if authorization is None:
            return
        authorization.run_id = run_id
        await session.commit()

    async def consume_authorization(
        self,
        *,
        user_id: str,
        agent_id: str,
        run_id: str,
        session_id: str,
        expected_authorization_id: str | None = None,
    ) -> ConsumedBrowserAuthorization:
        self._require_enabled()
        if expected_authorization_id is None:
            return await self._consume_scheduled_authorization(
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                session_id=session_id,
            )
        async with self.session_factory() as session:
            now = utc_now()
            await self._bind_chat_authorization_to_run(
                session=session,
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                run_id=run_id,
                now=now,
            )
            statement = (
                select(BrowserAccountRunAuthorizationRecord)
                .where(
                    col(BrowserAccountRunAuthorizationRecord.user_id) == user_id,
                    col(BrowserAccountRunAuthorizationRecord.agent_id) == agent_id,
                    col(BrowserAccountRunAuthorizationRecord.session_id) == session_id,
                    col(BrowserAccountRunAuthorizationRecord.run_id) == run_id,
                    col(BrowserAccountRunAuthorizationRecord.expires_at) > now,
                    col(BrowserAccountRunAuthorizationRecord.consumed_at).is_(None),
                    col(BrowserAccountRunAuthorizationRecord.revoked_at).is_(None),
                    *(
                        (col(BrowserAccountRunAuthorizationRecord.id) == expected_authorization_id,)
                        if expected_authorization_id is not None
                        else ()
                    ),
                )
                .order_by(col(BrowserAccountRunAuthorizationRecord.approved_at).desc())
                .with_for_update()
            )
            result = await session.exec(statement)
            authorization = result.first()
            if authorization is None:
                raise BrowserAccountAuthorizationError
            account = await self._require_account(
                session,
                user_id,
                authorization.browser_account_id,
            )
            if BrowserAccountStatus(account.status) != BrowserAccountStatus.READY:
                await self._audit_denial(
                    session=session,
                    account=account,
                    authorization=authorization,
                    run_id=run_id,
                )
                await session.commit()
                raise BrowserAccountAuthorizationError(ACCOUNT_NOT_READY)
            try:
                await self._require_account_grant(
                    session=session,
                    user_id=user_id,
                    account_id=account.id,
                    agent_id=agent_id,
                )
            except BrowserAccountAuthorizationError:
                await self._audit_denial(
                    session=session,
                    account=account,
                    authorization=authorization,
                    run_id=run_id,
                )
                await session.commit()
                raise
            authorization_id = authorization.id
            browser_account_id = account.id
            authorization.consumed_at = now
            authorization.consumed_run_id = run_id
            authorization.run_id = run_id
            try:
                await self._acquire_lease(
                    session=session,
                    account=account,
                    owner_id=authorization_id,
                    kind=BrowserAccountLeaseKind.TASK,
                    duration_seconds=self.settings.task_lease_seconds,
                )
            except BrowserAccountConflictError:
                await session.rollback()
                add_browser_account_audit_event(
                    session=session,
                    user_id=user_id,
                    browser_account_id=browser_account_id,
                    event_kind=BrowserAccountAuditEventKind.APPROVAL_DENIED,
                    outcome="denied",
                    authorization_id=authorization_id,
                    agent_id=agent_id,
                    run_id=run_id,
                    session_id=session_id,
                )
                await session.commit()
                raise
            await session.commit()
            return ConsumedBrowserAuthorization(
                authorization_id=authorization.id,
                audit_authorization_id=authorization.id,
                browser_account_id=account.id,
                provider_connection_id=account.provider_connection_id,
                provider=BrowserAccountProvider(account.provider),
                profile_origins=tuple(authorization.approved_origins),
                primary_origin=account.primary_origin,
                start_url=account.primary_origin,
                approved_task=authorization.task_summary,
                action_mode=BrowserTaskActionMode.READ_ONLY,
            )

    async def _pending_scheduled_authorization(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        agent_id: str,
        run_id: str,
    ) -> PendingBrowserAuthorization | None:
        scheduled = await self._scheduled_account(
            session=session,
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
        )
        if scheduled is None:
            return None
        _, account, _ = scheduled
        return PendingBrowserAuthorization(
            authorization_id=None,
            provider_connection_id=account.provider_connection_id,
            provider=BrowserAccountProvider(account.provider),
            execution_tool_name=self._execution_tool_name(account),
            required=True,
        )

    async def _consume_scheduled_authorization(
        self,
        *,
        user_id: str,
        agent_id: str,
        run_id: str,
        session_id: str,
    ) -> ConsumedBrowserAuthorization:
        async with self.session_factory() as session:
            scheduled = await self._scheduled_account(
                session=session,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                lock_run=True,
            )
            if scheduled is None:
                raise BrowserAccountAuthorizationError
            run, account, approved_task = scheduled
            if run.browser_account_execution_attempt == run.attempt:
                raise BrowserAccountAuthorizationError(
                    SCHEDULED_ACCOUNT_ALREADY_USED,
                )
            run.browser_account_execution_attempt = run.attempt
            await self._acquire_lease(
                session=session,
                account=account,
                owner_id=run_id,
                kind=BrowserAccountLeaseKind.TASK,
                duration_seconds=self.settings.task_lease_seconds,
            )
            add_browser_account_audit_event(
                session=session,
                user_id=user_id,
                browser_account_id=account.id,
                event_kind=BrowserAccountAuditEventKind.APPROVAL_CREATED,
                outcome="succeeded",
                agent_id=agent_id,
                run_id=run_id,
                session_id=session_id,
            )
            await session.commit()
            return ConsumedBrowserAuthorization(
                authorization_id=run_id,
                audit_authorization_id=None,
                browser_account_id=account.id,
                provider_connection_id=account.provider_connection_id,
                provider=BrowserAccountProvider(account.provider),
                profile_origins=(account.primary_origin, *account.authentication_origins),
                primary_origin=account.primary_origin,
                start_url=account.primary_origin,
                approved_task=approved_task,
                action_mode=BrowserTaskActionMode.READ_ONLY,
            )

    async def _scheduled_account(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        agent_id: str,
        run_id: str,
        lock_run: bool = False,
    ) -> tuple[AgentRunRecord, BrowserAccountRecord, str] | None:
        base_run_statement = select(AgentRunRecord).where(
            col(AgentRunRecord.id) == run_id,
            col(AgentRunRecord.user_id) == user_id,
            col(AgentRunRecord.agent_id) == agent_id,
        )
        run_statement = base_run_statement.with_for_update() if lock_run else base_run_statement
        run_result = await session.exec(run_statement)
        run = run_result.one_or_none()
        if run is None or run.schedule_id is None or run.browser_account_id is None:
            return None
        account = await self._require_account(
            session,
            user_id,
            run.browser_account_id,
        )
        if (
            BrowserAccountStatus(account.status) != BrowserAccountStatus.READY
            or BrowserAccountAuthenticationMode(account.authentication_mode)
            != BrowserAccountAuthenticationMode.STORED_CREDENTIALS
        ):
            raise BrowserAccountAuthorizationError(ACCOUNT_NOT_READY)
        await self._require_account_grant(
            session=session,
            user_id=user_id,
            account_id=account.id,
            agent_id=agent_id,
        )
        task = run.payload_json.get("text")
        if not isinstance(task, str) or not task.strip():
            raise BrowserAccountAuthorizationError
        return run, account, task.strip()

    def _execution_tool_name(self, account: BrowserAccountRecord) -> str:
        if BrowserAccountProvider(account.provider) == BrowserAccountProvider.STAGEHAND_LOCAL:
            return "use_browser"
        if BrowserAccountProvider(account.provider) == BrowserAccountProvider.BROWSERLESS:
            return "browserless_agent"
        return "run_session"

    async def execute_assigned_run_task(
        self,
        *,
        user_id: str,
        agent_id: str,
        account_id: str,
        session_id: str,
        run_id: str,
        instruction: str,
        start_url: str | None = None,
        action_mode: BrowserTaskActionMode = BrowserTaskActionMode.READ_ONLY,
    ) -> BrowserTaskResult:
        async with self.session_factory() as session:
            statement = select(AgentRunRecord).where(
                col(AgentRunRecord.id) == run_id,
                col(AgentRunRecord.user_id) == user_id,
                col(AgentRunRecord.agent_id) == agent_id,
                col(AgentRunRecord.session_id) == session_id,
            )
            result = await session.exec(statement)
            run = result.one_or_none()
            if run is None:
                raise BrowserAccountAuthorizationError
            client_turn_id = run.payload_json.get("client_turn_id")
            if not isinstance(client_turn_id, str) or not client_turn_id:
                raise BrowserAccountAuthorizationError(
                    ASSIGNED_ACCOUNT_TASK_INVALID,
                )
        return await self.execute_assigned_task(
            user_id=user_id,
            agent_id=agent_id,
            account_id=account_id,
            session_id=session_id,
            client_turn_id=client_turn_id,
            instruction=instruction,
            start_url=start_url,
            action_mode=action_mode,
            run_id=run_id,
        )

    async def execute_assigned_task(
        self,
        *,
        user_id: str,
        agent_id: str,
        account_id: str,
        session_id: str,
        client_turn_id: str,
        instruction: str,
        start_url: str | None = None,
        action_mode: BrowserTaskActionMode = BrowserTaskActionMode.READ_ONLY,
        run_id: str | None = None,
    ) -> BrowserTaskResult:
        normalized_task = instruction.strip()
        if not normalized_task or len(normalized_task) > 4096 or not client_turn_id or len(client_turn_id) > 64:
            raise BrowserAccountAuthorizationError(
                ASSIGNED_ACCOUNT_TASK_INVALID,
            )
        consumed = await self._consume_assigned_authorization(
            user_id=user_id,
            agent_id=agent_id,
            account_id=account_id,
            session_id=session_id,
            client_turn_id=client_turn_id,
            task=normalized_task,
            start_url=start_url,
            action_mode=action_mode,
            run_id=run_id,
        )
        return await self._execute_consumed_authorization(
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            run_id=run_id,
            consumed=consumed,
        )

    async def _consume_assigned_authorization(
        self,
        *,
        user_id: str,
        agent_id: str,
        account_id: str,
        session_id: str,
        client_turn_id: str,
        task: str,
        start_url: str | None,
        action_mode: BrowserTaskActionMode,
        run_id: str | None,
    ) -> ConsumedBrowserAuthorization:
        self._require_enabled()
        async with self.session_factory() as session:
            account = await self._require_account(
                session,
                user_id,
                account_id,
            )
            if BrowserAccountStatus(account.status) != BrowserAccountStatus.READY:
                raise BrowserAccountAuthorizationError(ACCOUNT_NOT_READY)
            await self._require_account_grant(
                session=session,
                user_id=user_id,
                account_id=account.id,
                agent_id=agent_id,
            )
            normalized_start_url = (
                account.primary_origin if start_url is None else normalize_browser_start_url(start_url)
            )
            profile_origins = (
                account.primary_origin,
                *account.authentication_origins,
            )
            if browser_url_origin(normalized_start_url) not in profile_origins:
                raise BrowserAccountAuthorizationError(
                    ASSIGNED_ACCOUNT_TASK_INVALID,
                )
            if BrowserAccountProvider(account.provider) != BrowserAccountProvider.STAGEHAND_LOCAL:
                if account.provider_connection_id is None:
                    raise BrowserAccountProviderConnectionError
                await self.credential_resolver.require_connection(
                    session=session,
                    user_id=user_id,
                    connection_id=account.provider_connection_id,
                )
            existing_statement = select(BrowserAccountRunAuthorizationRecord.id).where(
                col(BrowserAccountRunAuthorizationRecord.user_id) == user_id,
                col(BrowserAccountRunAuthorizationRecord.browser_account_id) == account.id,
                col(BrowserAccountRunAuthorizationRecord.session_id) == session_id,
                col(BrowserAccountRunAuthorizationRecord.client_turn_id) == client_turn_id,
                col(BrowserAccountRunAuthorizationRecord.consumed_at).is_not(None),
                col(BrowserAccountRunAuthorizationRecord.revoked_at).is_(None),
            )
            existing_result = await session.exec(existing_statement)
            if existing_result.first() is not None:
                raise BrowserAccountAuthorizationError(
                    ASSIGNED_ACCOUNT_ALREADY_USED,
                )
            now = utc_now()
            authorization = BrowserAccountRunAuthorizationRecord(
                user_id=user_id,
                browser_account_id=account.id,
                agent_id=agent_id,
                run_id=run_id,
                session_id=session_id,
                client_turn_id=client_turn_id,
                approved_origins=[
                    account.primary_origin,
                    *account.authentication_origins,
                ],
                task_summary=task,
                approved_by_user_id=user_id,
                approved_at=now,
                expires_at=now
                + timedelta(
                    seconds=self.settings.authorization_ttl_seconds,
                ),
                consumed_at=now,
                consumed_run_id=run_id,
            )
            session.add(authorization)
            await self._acquire_lease(
                session=session,
                account=account,
                owner_id=authorization.id,
                kind=BrowserAccountLeaseKind.TASK,
                duration_seconds=self.settings.task_lease_seconds,
            )
            add_browser_account_audit_event(
                session=session,
                user_id=user_id,
                browser_account_id=account.id,
                event_kind=BrowserAccountAuditEventKind.APPROVAL_CREATED,
                outcome="succeeded",
                authorization_id=authorization.id,
                agent_id=agent_id,
                run_id=run_id,
                session_id=session_id,
            )
            await session.commit()
            return ConsumedBrowserAuthorization(
                authorization_id=authorization.id,
                audit_authorization_id=authorization.id,
                browser_account_id=account.id,
                provider_connection_id=account.provider_connection_id,
                provider=BrowserAccountProvider(account.provider),
                profile_origins=(
                    account.primary_origin,
                    *account.authentication_origins,
                ),
                primary_origin=account.primary_origin,
                start_url=normalized_start_url,
                approved_task=task,
                action_mode=action_mode,
            )

    async def execute_authorized_task(
        self,
        *,
        user_id: str,
        agent_id: str,
        run_id: str,
        session_id: str,
        expected_authorization_id: str | None = None,
    ) -> BrowserTaskResult:
        consumed = await self.consume_authorization(
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            session_id=session_id,
            expected_authorization_id=expected_authorization_id,
        )
        return await self._execute_consumed_authorization(
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            run_id=run_id,
            consumed=consumed,
        )

    async def _execute_consumed_authorization(
        self,
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
        run_id: str | None,
        consumed: ConsumedBrowserAuthorization,
    ) -> BrowserTaskResult:
        async with self.session_factory() as session:
            account = await self._require_account(
                session,
                user_id,
                consumed.browser_account_id,
            )
            try:
                credential = await self._resolve_account_provider_credential(
                    session=session,
                    user_id=user_id,
                    account=account,
                )
                profile = await self.secret_store.load_profile_handle(
                    session=session,
                    user_id=user_id,
                    browser_account_id=account.id,
                )
                website_credentials = await self.secret_store.load_account_credentials(
                    session=session,
                    user_id=user_id,
                    browser_account_id=account.id,
                )
                model_credential = None
                if consumed.provider == BrowserAccountProvider.STAGEHAND_LOCAL:
                    if self.model_credential_resolver is None:
                        raise BrowserProviderOperationError(
                            BrowserAccountErrorCategory.PROVIDER_AUTH,
                        )
                    model_credential = await self.model_credential_resolver.resolve(
                        user_id=user_id,
                    )
            except (Exception, asyncio.CancelledError):
                await self._release_lease(session, account.id)
                await session.commit()
                raise
            if consumed.provider == BrowserAccountProvider.BROWSER_USE_CLOUD and profile is None:
                account.status = BrowserAccountStatus.EXPIRED.value
                account.error_category = BrowserAccountErrorCategory.PROFILE_MISSING.value
                await self._release_lease(session, account.id)
                await session.commit()
                raise BrowserAccountAuthorizationError(
                    ACCOUNT_REAUTHENTICATION_REQUIRED,
                )
            if self._uses_stored_credentials(consumed.provider) and website_credentials is None:
                account.status = BrowserAccountStatus.EXPIRED.value
                account.error_category = BrowserAccountErrorCategory.PROVIDER_AUTH.value
                await self._release_lease(session, account.id)
                await session.commit()
                raise BrowserAccountAuthorizationError(
                    ACCOUNT_REAUTHENTICATION_REQUIRED,
                )
            add_browser_account_audit_event(
                session=session,
                user_id=user_id,
                browser_account_id=account.id,
                event_kind=BrowserAccountAuditEventKind.AUTHORITY_UNSEALED,
                outcome="succeeded",
                authorization_id=consumed.audit_authorization_id,
                agent_id=agent_id,
                run_id=run_id,
                session_id=session_id,
            )
            await session.commit()

            variables: dict[str, BrowserTaskVariable] = {}
            if website_credentials is not None:
                variables = {
                    "login_identifier": BrowserTaskVariable(
                        value=website_credentials.username,
                        description="The approved website username or email",
                    ),
                    "login_password": BrowserTaskVariable(
                        value=website_credentials.password,
                        description="The approved website password",
                    ),
                }
            task_request = BrowserTaskRequest(
                instruction=consumed.approved_task,
                start_url=consumed.start_url,
                allowed_origins=consumed.profile_origins,
                action_mode=consumed.action_mode,
                browser_country_code=account.browser_country_code,
                max_steps=self.settings.max_task_steps,
                variables=variables,
                model_credential=model_credential,
            )
            provider_task = None
            try:
                provider_task = await self.provider.start_task(
                    credential=credential,
                    profile=profile,
                    request=task_request,
                )
                await self.secret_store.seal_session_handle(
                    session=session,
                    user_id=user_id,
                    credential_id=consumed.authorization_id,
                    handle=provider_task.session_handle,
                    provider=consumed.provider,
                )
                add_browser_account_audit_event(
                    session=session,
                    user_id=user_id,
                    browser_account_id=account.id,
                    event_kind=BrowserAccountAuditEventKind.SESSION_STARTED,
                    outcome="succeeded",
                    authorization_id=consumed.audit_authorization_id,
                    agent_id=agent_id,
                    run_id=run_id,
                    session_id=session_id,
                )
                await session.commit()
                result = await self._wait_for_provider_task(
                    credential=credential,
                    task=provider_task,
                    user_id=user_id,
                    account_id=account.id,
                    lease_owner_id=consumed.authorization_id,
                )
                account.error_category = None
                if not result.successful:
                    account.error_category = BrowserAccountErrorCategory.PROVIDER_REJECTED.value
                    add_browser_account_audit_event(
                        session=session,
                        user_id=user_id,
                        browser_account_id=account.id,
                        event_kind=BrowserAccountAuditEventKind.PROVIDER_ERROR,
                        outcome="failed",
                        authorization_id=consumed.audit_authorization_id,
                        agent_id=agent_id,
                        run_id=run_id,
                        session_id=session_id,
                        error_category=BrowserAccountErrorCategory.PROVIDER_REJECTED,
                    )
                return result
            except BrowserProviderOperationError as exc:
                account.error_category = exc.category.value
                if exc.category == BrowserAccountErrorCategory.PROFILE_MISSING:
                    account.status = BrowserAccountStatus.EXPIRED.value
                add_browser_account_audit_event(
                    session=session,
                    user_id=user_id,
                    browser_account_id=account.id,
                    event_kind=BrowserAccountAuditEventKind.PROVIDER_ERROR,
                    outcome="failed",
                    authorization_id=consumed.audit_authorization_id,
                    agent_id=agent_id,
                    run_id=run_id,
                    session_id=session_id,
                    error_category=exc.category,
                )
                await session.commit()
                raise BrowserAccountProviderError(exc.category) from exc
            finally:
                cleanup_task = asyncio.create_task(
                    self._finalize_retrieval_session(
                        session=session,
                        account=account,
                        credential=credential,
                        provider_task=provider_task,
                        user_id=user_id,
                        session_credential_id=consumed.authorization_id,
                        audit_authorization_id=consumed.audit_authorization_id,
                        agent_id=agent_id,
                        run_id=run_id,
                        session_id=session_id,
                    ),
                )
                await self._await_cleanup_task(cleanup_task)

    async def _finalize_retrieval_session(
        self,
        *,
        session: AsyncSession,
        account: BrowserAccountRecord,
        credential: BrowserProviderCredential,
        provider_task: BrowserProviderTaskSession | None,
        user_id: str,
        session_credential_id: str,
        audit_authorization_id: str | None,
        agent_id: str,
        run_id: str | None,
        session_id: str,
    ) -> None:
        cleanup_succeeded = False
        if provider_task is not None:
            try:
                await self._stop_session(
                    credential,
                    provider_task.session_handle,
                )
                cleanup_succeeded = True
            except BrowserProviderOperationError as cleanup_error:
                if account.error_category is None:
                    account.error_category = BrowserAccountErrorCategory.CLEANUP_FAILED.value
                logger.exception(
                    "browser account session cleanup failed",
                    extra={
                        "browser_account_id": account.id,
                        "provider": account.provider,
                        "origin": account.primary_origin,
                        "operation": "stop_task_session",
                        "error_category": cleanup_error.category.value,
                    },
                )
                add_browser_account_audit_event(
                    session=session,
                    user_id=user_id,
                    browser_account_id=account.id,
                    event_kind=BrowserAccountAuditEventKind.PROVIDER_ERROR,
                    outcome="failed",
                    authorization_id=audit_authorization_id,
                    agent_id=agent_id,
                    run_id=run_id,
                    session_id=session_id,
                    error_category=BrowserAccountErrorCategory.CLEANUP_FAILED,
                )
        if cleanup_succeeded:
            await self.secret_store.delete_session_handle(
                session=session,
                user_id=user_id,
                credential_id=session_credential_id,
            )
            await self._release_lease(session, account.id)
            account.last_used_at = utc_now()
            account.updated_at = utc_now()
            add_browser_account_audit_event(
                session=session,
                user_id=user_id,
                browser_account_id=account.id,
                event_kind=BrowserAccountAuditEventKind.SESSION_STOPPED,
                outcome="succeeded",
                authorization_id=audit_authorization_id,
                agent_id=agent_id,
                run_id=run_id,
                session_id=session_id,
            )
        elif provider_task is None:
            await self._release_lease(session, account.id)
        await session.commit()

    @staticmethod
    async def _await_cleanup_task(
        cleanup_task: asyncio.Task[None],
    ) -> None:
        cancellation: asyncio.CancelledError | None = None
        while True:
            try:
                await asyncio.shield(cleanup_task)
                break
            except asyncio.CancelledError as exc:
                if cleanup_task.cancelled():
                    raise
                cancellation = cancellation or exc
        if cancellation is not None:
            raise cancellation

    async def _wait_for_provider_task(
        self,
        *,
        credential: BrowserProviderCredential,
        task: BrowserProviderTaskSession,
        user_id: str,
        account_id: str,
        lease_owner_id: str,
    ) -> BrowserTaskResult:
        provider_wait = asyncio.create_task(
            self.provider.wait_for_task(
                credential=credential,
                task=task,
            ),
        )
        try:
            while True:
                done, _ = await asyncio.wait(
                    (provider_wait,),
                    timeout=self.settings.poll_interval_seconds,
                )
                if done:
                    result = await provider_wait
                    if result.output_text.strip():
                        return result
                    return BrowserTaskResult(
                        output_text=UNUSABLE_BROWSER_TASK_RESULT,
                        successful=False,
                    )
                if not await self._heartbeat_task_lease(
                    user_id=user_id,
                    account_id=account_id,
                    owner_id=lease_owner_id,
                ):
                    raise BrowserProviderOperationError(
                        BrowserAccountErrorCategory.PROVIDER_REJECTED,
                    )
        finally:
            if not provider_wait.done():
                provider_wait.cancel()
            await asyncio.gather(provider_wait, return_exceptions=True)

    async def _heartbeat_task_lease(
        self,
        *,
        user_id: str,
        account_id: str,
        owner_id: str,
    ) -> bool:
        now = utc_now()
        async with self.session_factory() as session:
            account_statement = select(BrowserAccountRecord).where(
                col(BrowserAccountRecord.id) == account_id,
                col(BrowserAccountRecord.user_id) == user_id,
                col(BrowserAccountRecord.deleted_at).is_(None),
            )
            account_result = await session.exec(account_statement)
            account = account_result.one_or_none()
            if account is None or BrowserAccountStatus(account.status) != BrowserAccountStatus.READY:
                return False
            lease_statement = (
                select(BrowserAccountLeaseRecord)
                .where(
                    col(BrowserAccountLeaseRecord.user_id) == user_id,
                    col(BrowserAccountLeaseRecord.browser_account_id) == account_id,
                    col(BrowserAccountLeaseRecord.owner_id) == owner_id,
                    col(BrowserAccountLeaseRecord.lease_kind) == BrowserAccountLeaseKind.TASK.value,
                    col(BrowserAccountLeaseRecord.active).is_(True),
                )
                .with_for_update()
            )
            lease_result = await session.exec(lease_statement)
            lease = lease_result.one_or_none()
            if lease is None:
                return False
            lease.heartbeat_at = now
            lease.expires_at = now + timedelta(
                seconds=self.settings.task_lease_seconds,
            )
            await session.commit()
            return True

    async def revoke_account(
        self,
        *,
        user_id: str,
        account_id: str,
        delete_after: bool = False,
    ) -> BrowserAccountResponse | None:
        self._require_enabled()
        async with self.session_factory() as session:
            account = await self._require_account(
                session,
                user_id,
                account_id,
                include_deleted=delete_after,
            )
            if account.deleted_at is not None:
                return None
            now = utc_now()
            if delete_after and account.delete_requested_at is None:
                account.delete_requested_at = now
            should_delete = account.delete_requested_at is not None
            account.status = BrowserAccountStatus.REVOKED.value
            account.error_category = None
            account.updated_at = now
            account.version += 1
            add_browser_account_audit_event(
                session=session,
                user_id=user_id,
                browser_account_id=account.id,
                event_kind=BrowserAccountAuditEventKind.REVOKED,
                outcome="succeeded",
            )
            await session.commit()

            try:
                credential = await self._resolve_account_provider_credential(
                    session=session,
                    user_id=user_id,
                    account=account,
                    require_active=False,
                )
                if self._uses_stored_credentials(
                    BrowserAccountProvider(account.provider),
                ):
                    if not await self._wait_for_task_lease_release(
                        user_id=user_id,
                        account_id=account.id,
                    ):
                        raise BrowserProviderOperationError(
                            BrowserAccountErrorCategory.CLEANUP_FAILED,
                        )
                    await self._discard_browserless_session_references(
                        session=session,
                        account=account,
                    )
                else:
                    await self._stop_account_sessions(
                        session=session,
                        account=account,
                        credential=credential,
                    )
                profile = await self.secret_store.load_profile_handle(
                    session=session,
                    user_id=user_id,
                    browser_account_id=account.id,
                )
                if profile is not None:
                    await self.provider.delete_profile(
                        credential=credential,
                        profile=profile,
                    )
                    await self.secret_store.delete_profile_handle(
                        session=session,
                        user_id=user_id,
                        browser_account_id=account.id,
                    )
                await self.secret_store.delete_account_credentials(
                    session=session,
                    user_id=user_id,
                    browser_account_id=account.id,
                )
                await self._release_lease(session, account.id)
                if should_delete:
                    account.deleted_at = utc_now()
                add_browser_account_audit_event(
                    session=session,
                    user_id=user_id,
                    browser_account_id=account.id,
                    event_kind=BrowserAccountAuditEventKind.REMOTE_DELETED,
                    outcome="succeeded",
                )
                await session.commit()
            except asyncio.CancelledError:
                await self._mark_deletion_pending(
                    session=session,
                    user_id=user_id,
                    account_id=account_id,
                    category=BrowserAccountErrorCategory.UNKNOWN,
                )
                raise
            except (BrowserProviderOperationError, BrowserAccountProviderConnectionError) as exc:
                category = (
                    exc.category
                    if isinstance(exc, BrowserProviderOperationError)
                    else BrowserAccountErrorCategory.PROVIDER_AUTH
                )
                await self._mark_deletion_pending(
                    session=session,
                    user_id=user_id,
                    account_id=account_id,
                    category=category,
                )
                raise BrowserAccountProviderError(category) from exc
            except Exception as exc:
                logger.exception(
                    "browser account remote deletion failed unexpectedly",
                    extra={
                        "browser_account_id": account_id,
                        "operation": "delete_remote_profile",
                    },
                )
                await self._mark_deletion_pending(
                    session=session,
                    user_id=user_id,
                    account_id=account_id,
                    category=BrowserAccountErrorCategory.UNKNOWN,
                )
                raise BrowserAccountProviderError(
                    BrowserAccountErrorCategory.UNKNOWN,
                ) from exc

            if should_delete:
                return None
            await session.refresh(account)
            return await self._response(session, account)

    async def _wait_for_task_lease_release(
        self,
        *,
        user_id: str,
        account_id: str,
    ) -> bool:
        timeout_seconds = self.settings.cleanup_timeout_seconds + (2 * self.settings.poll_interval_seconds)
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            async with self.session_factory() as session:
                statement = select(BrowserAccountLeaseRecord.id).where(
                    col(BrowserAccountLeaseRecord.user_id) == user_id,
                    col(BrowserAccountLeaseRecord.browser_account_id) == account_id,
                    col(BrowserAccountLeaseRecord.lease_kind) == BrowserAccountLeaseKind.TASK.value,
                    col(BrowserAccountLeaseRecord.active).is_(True),
                )
                result = await session.exec(statement)
                if result.one_or_none() is None:
                    return True
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            await asyncio.sleep(
                min(self.settings.poll_interval_seconds, remaining),
            )

    async def _discard_browserless_session_references(
        self,
        *,
        session: AsyncSession,
        account: BrowserAccountRecord,
    ) -> None:
        statement = select(BrowserAccountLeaseRecord).where(
            col(BrowserAccountLeaseRecord.user_id) == account.user_id,
            col(BrowserAccountLeaseRecord.browser_account_id) == account.id,
        )
        result = await session.exec(statement)
        lease = result.one_or_none()
        credential_ids = [account.id]
        if lease is not None and lease.lease_kind == BrowserAccountLeaseKind.TASK.value:
            credential_ids.append(lease.owner_id)
        for credential_id in dict.fromkeys(credential_ids):
            await self.secret_store.delete_session_handle(
                session=session,
                user_id=account.user_id,
                credential_id=credential_id,
            )

    async def reconcile_user(self, *, user_id: str) -> None:
        if not self.settings.enabled:
            return
        async with self.session_factory() as session:
            now = utc_now()
            expired_leases = select(BrowserAccountLeaseRecord).where(
                col(BrowserAccountLeaseRecord.user_id) == user_id,
                col(BrowserAccountLeaseRecord.expires_at) <= now,
                col(BrowserAccountLeaseRecord.active).is_(True),
            )
            lease_result = await session.exec(expired_leases)
            for lease in lease_result.all():
                account = await session.get(BrowserAccountRecord, lease.browser_account_id)
                if account is None or account.user_id != user_id:
                    lease.active = False
                    continue
                credential_id = (
                    account.id if lease.lease_kind == BrowserAccountLeaseKind.SETUP.value else lease.owner_id
                )
                try:
                    if self._uses_stored_credentials(
                        BrowserAccountProvider(account.provider),
                    ):
                        await self.secret_store.delete_session_handle(
                            session=session,
                            user_id=user_id,
                            credential_id=credential_id,
                        )
                    else:
                        credential = await self._resolve_account_provider_credential(
                            session=session,
                            user_id=user_id,
                            account=account,
                            require_active=False,
                        )
                        await self._stop_stored_session(
                            session=session,
                            user_id=user_id,
                            credential_id=credential_id,
                            credential=credential,
                        )
                    lease.active = False
                    lease.heartbeat_at = now
                    add_browser_account_audit_event(
                        session=session,
                        user_id=user_id,
                        browser_account_id=account.id,
                        event_kind=BrowserAccountAuditEventKind.RECONCILED,
                        outcome="succeeded",
                    )
                except (
                    BrowserProviderOperationError,
                    BrowserAccountProviderConnectionError,
                ):
                    lease.expires_at = now + timedelta(
                        seconds=self.settings.task_lease_seconds,
                    )
                    lease.heartbeat_at = now
                    account.status = BrowserAccountStatus.ERROR.value
                    account.error_category = BrowserAccountErrorCategory.CLEANUP_FAILED.value
                    add_browser_account_audit_event(
                        session=session,
                        user_id=user_id,
                        browser_account_id=account.id,
                        event_kind=BrowserAccountAuditEventKind.RECONCILED,
                        outcome="failed",
                        error_category=BrowserAccountErrorCategory.CLEANUP_FAILED,
                    )
            stale_setup_cutoff = now - timedelta(seconds=self.settings.setup_ttl_seconds)
            stale_setups = select(BrowserAccountRecord).where(
                col(BrowserAccountRecord.user_id) == user_id,
                col(BrowserAccountRecord.status) == BrowserAccountStatus.SETUP_IN_PROGRESS.value,
                col(BrowserAccountRecord.setup_started_at) <= stale_setup_cutoff,
            )
            setup_result = await session.exec(stale_setups)
            for account in setup_result.all():
                account.status = BrowserAccountStatus.ERROR.value
                account.error_category = BrowserAccountErrorCategory.SESSION_TIMEOUT.value
                account.updated_at = now
                add_browser_account_audit_event(
                    session=session,
                    user_id=user_id,
                    browser_account_id=account.id,
                    event_kind=BrowserAccountAuditEventKind.RECONCILED,
                    outcome="failed",
                    error_category=BrowserAccountErrorCategory.SESSION_TIMEOUT,
                )
            await session.commit()

            deletion_pending_statement = select(BrowserAccountRecord.id).where(
                col(BrowserAccountRecord.user_id) == user_id,
                col(BrowserAccountRecord.status) == BrowserAccountStatus.DELETION_PENDING.value,
                col(BrowserAccountRecord.deleted_at).is_(None),
            )
            deletion_pending_result = await session.exec(
                deletion_pending_statement,
            )
            deletion_pending_ids = tuple(deletion_pending_result.all())
            record_remote_deletion_backlog(len(deletion_pending_ids))

        for account_id in deletion_pending_ids:
            try:
                await self.revoke_account(
                    user_id=user_id,
                    account_id=account_id,
                )
            except BrowserAccountProviderError:
                logger.exception(
                    "browser account remote deletion reconciliation failed",
                    extra={
                        "browser_account_id": account_id,
                        "operation": "retry_remote_deletion",
                    },
                )

    async def _handle_setup_failure(
        self,
        *,
        session: AsyncSession,
        account: BrowserAccountRecord,
        credential: BrowserProviderCredential,
        profile: OpaqueBrowserProfileHandle | None,
        created_profile: bool,
        category: BrowserAccountErrorCategory,
    ) -> None:
        if created_profile and profile is not None:
            try:
                await self.provider.delete_profile(
                    credential=credential,
                    profile=profile,
                )
                await self.secret_store.delete_profile_handle(
                    session=session,
                    user_id=account.user_id,
                    browser_account_id=account.id,
                )
            except BrowserProviderOperationError:
                logger.exception(
                    "browser account profile rollback failed",
                    extra={
                        "browser_account_id": account.id,
                        "provider": account.provider,
                        "origin": account.primary_origin,
                        "operation": "rollback_profile",
                    },
                )
        elif category == BrowserAccountErrorCategory.PROFILE_MISSING:
            await self.secret_store.delete_profile_handle(
                session=session,
                user_id=account.user_id,
                browser_account_id=account.id,
            )
        await self._release_lease(session, account.id)
        account.status = BrowserAccountStatus.ERROR.value
        account.error_category = category.value
        account.setup_started_at = None
        account.updated_at = utc_now()
        add_browser_account_audit_event(
            session=session,
            user_id=account.user_id,
            browser_account_id=account.id,
            event_kind=BrowserAccountAuditEventKind.PROVIDER_ERROR,
            outcome="failed",
            error_category=category,
        )
        await session.commit()

    async def _stop_stored_session(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        credential_id: str,
        credential: BrowserProviderCredential,
    ) -> None:
        stored_session = await self.secret_store.load_session_handle(
            session=session,
            user_id=user_id,
            credential_id=credential_id,
        )
        if stored_session is None:
            return
        await self._stop_session(credential, stored_session)
        await self.secret_store.delete_session_handle(
            session=session,
            user_id=user_id,
            credential_id=credential_id,
        )

    async def _stop_account_sessions(
        self,
        *,
        session: AsyncSession,
        account: BrowserAccountRecord,
        credential: BrowserProviderCredential,
    ) -> None:
        statement = select(BrowserAccountLeaseRecord).where(
            col(BrowserAccountLeaseRecord.browser_account_id) == account.id,
            col(BrowserAccountLeaseRecord.active).is_(True),
        )
        result = await session.exec(statement)
        lease = result.one_or_none()
        credential_ids = [account.id]
        if lease is not None and lease.lease_kind == BrowserAccountLeaseKind.TASK.value:
            credential_ids.insert(0, lease.owner_id)
        for credential_id in dict.fromkeys(credential_ids):
            await self._stop_stored_session(
                session=session,
                user_id=account.user_id,
                credential_id=credential_id,
                credential=credential,
            )

    async def _mark_deletion_pending(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        account_id: str,
        category: BrowserAccountErrorCategory,
    ) -> None:
        await session.rollback()
        account = await self._require_account(session, user_id, account_id)
        account.status = BrowserAccountStatus.DELETION_PENDING.value
        account.error_category = category.value
        account.updated_at = utc_now()
        add_browser_account_audit_event(
            session=session,
            user_id=user_id,
            browser_account_id=account.id,
            event_kind=BrowserAccountAuditEventKind.DELETION_PENDING,
            outcome="failed",
            error_category=category,
        )
        await session.commit()

    async def _stop_session(
        self,
        credential: BrowserProviderCredential,
        provider_session: OpaqueBrowserSessionHandle,
    ) -> None:
        stop_task = asyncio.create_task(
            self.provider.stop_session(
                credential=credential,
                session=provider_session,
            ),
        )
        try:
            async with asyncio.timeout(self.settings.cleanup_timeout_seconds):
                await asyncio.shield(stop_task)
        except TimeoutError as exc:
            await self._cancel_stop_task(stop_task)
            raise BrowserProviderOperationError(
                BrowserAccountErrorCategory.CLEANUP_FAILED,
            ) from exc
        except asyncio.CancelledError:
            try:
                async with asyncio.timeout(self.settings.cleanup_timeout_seconds):
                    await asyncio.shield(stop_task)
            except (TimeoutError, Exception):
                await self._cancel_stop_task(stop_task)
            raise

    async def _cancel_stop_task(self, stop_task: asyncio.Task[None]) -> None:
        if not stop_task.done():
            stop_task.cancel()
        try:
            await stop_task
        except (asyncio.CancelledError, Exception):
            return

    async def _acquire_lease(
        self,
        *,
        session: AsyncSession,
        account: BrowserAccountRecord,
        owner_id: str,
        kind: BrowserAccountLeaseKind,
        duration_seconds: int,
    ) -> None:
        now = utc_now()
        statement = (
            select(BrowserAccountLeaseRecord)
            .where(
                col(BrowserAccountLeaseRecord.browser_account_id) == account.id,
            )
            .with_for_update()
        )
        result = await session.exec(statement)
        lease = result.one_or_none()
        if lease is not None and lease.active and as_utc(lease.expires_at) > now:
            raise BrowserAccountConflictError(ACCOUNT_ALREADY_IN_USE)
        if lease is not None and lease.active:
            await self._reconcile_expired_lease(
                session=session,
                account=account,
                lease=lease,
            )
        if lease is None:
            lease = BrowserAccountLeaseRecord(
                user_id=account.user_id,
                browser_account_id=account.id,
                owner_id=owner_id,
                lease_kind=kind.value,
                expires_at=now + timedelta(seconds=duration_seconds),
                heartbeat_at=now,
                active=True,
            )
            session.add(lease)
        else:
            lease.owner_id = owner_id
            lease.lease_kind = kind.value
            lease.expires_at = now + timedelta(seconds=duration_seconds)
            lease.heartbeat_at = now
            lease.active = True
        try:
            await session.flush()
        except IntegrityError as exc:
            raise BrowserAccountConflictError(
                ACCOUNT_ALREADY_IN_USE,
            ) from exc

    async def _reconcile_expired_lease(
        self,
        *,
        session: AsyncSession,
        account: BrowserAccountRecord,
        lease: BrowserAccountLeaseRecord,
    ) -> None:
        credential_id = account.id if lease.lease_kind == BrowserAccountLeaseKind.SETUP.value else lease.owner_id
        try:
            stored_session = await self.secret_store.load_session_handle(
                session=session,
                user_id=account.user_id,
                credential_id=credential_id,
            )
            if stored_session is not None:
                if not self._uses_stored_credentials(
                    BrowserAccountProvider(account.provider),
                ):
                    credential = await self._resolve_account_provider_credential(
                        session=session,
                        user_id=account.user_id,
                        account=account,
                        require_active=False,
                    )
                    await self._stop_session(credential, stored_session)
                await self.secret_store.delete_session_handle(
                    session=session,
                    user_id=account.user_id,
                    credential_id=credential_id,
                )
        except (
            BrowserProviderOperationError,
            BrowserAccountProviderConnectionError,
        ) as exc:
            raise BrowserAccountConflictError(ACCOUNT_ALREADY_IN_USE) from exc
        lease.active = False
        lease.heartbeat_at = utc_now()
        add_browser_account_audit_event(
            session=session,
            user_id=account.user_id,
            browser_account_id=account.id,
            event_kind=BrowserAccountAuditEventKind.RECONCILED,
            outcome="succeeded",
        )

    async def _release_lease(
        self,
        session: AsyncSession,
        account_id: str,
    ) -> None:
        statement = select(BrowserAccountLeaseRecord).where(
            col(BrowserAccountLeaseRecord.browser_account_id) == account_id,
        )
        result = await session.exec(statement)
        lease = result.one_or_none()
        if lease is not None:
            lease.active = False
            lease.heartbeat_at = utc_now()

    async def _require_account(
        self,
        session: AsyncSession,
        user_id: str,
        account_id: str,
        *,
        include_deleted: bool = False,
    ) -> BrowserAccountRecord:
        conditions = [
            col(BrowserAccountRecord.id) == account_id,
            col(BrowserAccountRecord.user_id) == user_id,
        ]
        if not include_deleted:
            conditions.append(col(BrowserAccountRecord.deleted_at).is_(None))
        statement = select(BrowserAccountRecord).where(*conditions)
        result = await session.exec(statement)
        account = result.one_or_none()
        if account is None:
            raise BrowserAccountNotFoundError
        return account

    async def _require_agents(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        agent_ids: tuple[str, ...],
    ) -> None:
        if not agent_ids:
            return
        statement = select(UserAgentRecord.id).where(
            col(UserAgentRecord.owner_user_id) == user_id,
            col(UserAgentRecord.id).in_(agent_ids),
        )
        result = await session.exec(statement)
        if set(result.all()) != set(agent_ids):
            raise BrowserAccountAuthorizationError(AGENT_NOT_FOUND)

    async def _require_chat(
        self,
        session: AsyncSession,
        user_id: str,
        chat_id: str,
        *,
        for_update: bool = False,
    ) -> AgentSessionRecord:
        statement = select(AgentSessionRecord).where(
            col(AgentSessionRecord.id) == chat_id,
            col(AgentSessionRecord.user_id) == user_id,
            col(AgentSessionRecord.deleted_at).is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        result = await session.exec(statement)
        chat = result.one_or_none()
        if chat is None:
            raise BrowserAccountAuthorizationError(CHAT_NOT_FOUND)
        return chat

    async def _require_account_grant(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        account_id: str,
        agent_id: str,
    ) -> None:
        statement = select(AgentBrowserAccountGrantRecord.id).where(
            col(AgentBrowserAccountGrantRecord.user_id) == user_id,
            col(AgentBrowserAccountGrantRecord.browser_account_id) == account_id,
            col(AgentBrowserAccountGrantRecord.agent_id) == agent_id,
        )
        result = await session.exec(statement)
        if result.one_or_none() is None:
            raise BrowserAccountAuthorizationError(
                AGENT_ACCOUNT_GRANT_REQUIRED,
            )

    async def _audit_denial(
        self,
        *,
        session: AsyncSession,
        account: BrowserAccountRecord,
        authorization: BrowserAccountRunAuthorizationRecord,
        run_id: str,
    ) -> None:
        add_browser_account_audit_event(
            session=session,
            user_id=account.user_id,
            browser_account_id=account.id,
            event_kind=BrowserAccountAuditEventKind.APPROVAL_DENIED,
            outcome="denied",
            authorization_id=authorization.id,
            agent_id=authorization.agent_id,
            run_id=run_id,
            session_id=authorization.session_id,
        )

    async def _response(
        self,
        session: AsyncSession,
        account: BrowserAccountRecord,
    ) -> BrowserAccountResponse:
        grants = await self._grants_for_accounts(
            session=session,
            user_id=account.user_id,
            account_ids=(account.id,),
        )
        credential_account_ids = await self._credential_account_ids(
            session=session,
            user_id=account.user_id,
            account_ids=(account.id,),
        )
        return self._response_from_record(
            account,
            grants.get(account.id, ()),
            has_credentials=account.id in credential_account_ids,
        )

    async def _credential_account_ids(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        account_ids: tuple[str, ...],
    ) -> frozenset[str]:
        if not account_ids:
            return frozenset()
        statement = select(UserSecretRecord.credential_id).where(
            col(UserSecretRecord.user_id) == user_id,
            col(UserSecretRecord.provider) == BROWSER_ACCOUNT_CREDENTIAL_ENCRYPTION_PROVIDER,
            col(UserSecretRecord.purpose) == SecretPurpose.BROWSER_ACCOUNT_CREDENTIAL.value,
            col(UserSecretRecord.credential_id).in_(account_ids),
        )
        result = await session.exec(statement)
        return frozenset(result.all())

    async def _grants_for_accounts(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        account_ids: tuple[str, ...],
    ) -> dict[str, tuple[str, ...]]:
        if not account_ids:
            return {}
        statement = select(AgentBrowserAccountGrantRecord).where(
            col(AgentBrowserAccountGrantRecord.user_id) == user_id,
            col(AgentBrowserAccountGrantRecord.browser_account_id).in_(account_ids),
        )
        result = await session.exec(statement)
        grouped: dict[str, list[str]] = {}
        for grant in result.all():
            grouped.setdefault(grant.browser_account_id, []).append(grant.agent_id)
        return {account_id: tuple(sorted(agent_ids)) for account_id, agent_ids in grouped.items()}

    async def _resolve_requested_provider_credential(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        request: BrowserAccountCreateRequest,
    ) -> BrowserProviderCredential:
        if request.provider_connection_id is None:
            provider = request.provider or BrowserAccountProvider.STAGEHAND_LOCAL
            if provider != BrowserAccountProvider.STAGEHAND_LOCAL:
                raise BrowserAccountValidationError(
                    HOSTED_CONNECTION_REQUIRED,
                )
            return BrowserProviderCredential(
                provider=BrowserAccountProvider.STAGEHAND_LOCAL,
            )
        credential = await self.credential_resolver.resolve_credential(
            session=session,
            user_id=user_id,
            connection_id=request.provider_connection_id,
        )
        if request.provider is not None and request.provider != credential.provider:
            raise BrowserAccountValidationError(
                PROVIDER_CONNECTION_MISMATCH,
            )
        return credential

    async def _resolve_account_provider_credential(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        account: BrowserAccountRecord,
        require_active: bool = True,
    ) -> BrowserProviderCredential:
        provider = BrowserAccountProvider(account.provider)
        if provider == BrowserAccountProvider.STAGEHAND_LOCAL:
            return BrowserProviderCredential(provider=provider)
        if account.provider_connection_id is None:
            raise BrowserAccountProviderConnectionError
        return await self.credential_resolver.resolve_credential(
            session=session,
            user_id=user_id,
            connection_id=account.provider_connection_id,
            require_active=require_active,
        )

    @staticmethod
    def _uses_stored_credentials(provider: BrowserAccountProvider) -> bool:
        return provider in {
            BrowserAccountProvider.BROWSERLESS,
            BrowserAccountProvider.STAGEHAND_LOCAL,
        }

    def _response_from_record(
        self,
        account: BrowserAccountRecord,
        agent_ids: tuple[str, ...],
        *,
        has_credentials: bool,
    ) -> BrowserAccountResponse:
        return BrowserAccountResponse(
            id=account.id,
            label=account.label,
            provider=BrowserAccountProvider(account.provider),
            provider_connection_id=account.provider_connection_id,
            primary_origin=account.primary_origin,
            authentication_origins=tuple(account.authentication_origins),
            browser_country_code=account.browser_country_code,
            authentication_mode=BrowserAccountAuthenticationMode(
                account.authentication_mode,
            ),
            status=BrowserAccountStatus(account.status),
            error_category=(
                None if account.error_category is None else BrowserAccountErrorCategory(account.error_category)
            ),
            agent_ids=agent_ids,
            has_credentials=has_credentials,
            last_verified_at=account.last_verified_at,
            last_used_at=account.last_used_at,
            created_at=account.created_at,
            updated_at=account.updated_at,
        )

    def _authorization_response(
        self,
        authorization: BrowserAccountRunAuthorizationRecord,
        account: BrowserAccountRecord,
    ) -> BrowserAccountAuthorizationResponse:
        return BrowserAccountAuthorizationResponse(
            id=authorization.id,
            account_id=account.id,
            account_label=account.label,
            provider=BrowserAccountProvider(account.provider),
            agent_id=authorization.agent_id,
            session_id=authorization.session_id,
            approved_origins=tuple(authorization.approved_origins),
            task_summary=authorization.task_summary,
            approved_at=authorization.approved_at,
            expires_at=authorization.expires_at,
            consumed_at=authorization.consumed_at,
        )

    def _require_enabled(self) -> None:
        if not self.settings.enabled:
            raise BrowserAccountsDisabledError


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value

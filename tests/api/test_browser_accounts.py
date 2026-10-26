from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.requests import Request

from voxos.agent_identity.records import UserAgentRecord
from voxos.agent_runtime.records import AgentSessionRecord
from voxos.api.browser_accounts import create_browser_accounts_router
from voxos.auth.models import AuthenticatedUser
from voxos.browser_accounts.providers.base import (
    BrowserProviderCapabilities,
    BrowserProviderCredential,
    BrowserProviderSetupSession,
    BrowserProviderTaskSession,
)
from voxos.browser_accounts.secrets import OpaqueBrowserProfileHandle, OpaqueBrowserSessionHandle
from voxos.browser_accounts.settings import BrowserAccountSettings
from voxos.browser_runtime.contracts import BrowserTaskRequest, BrowserTaskResult
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.mcp.models import McpSecretKind, McpServerSafeMetadata
from voxos.mcp.records import UserMcpServerRecord
from voxos.mcp.services import McpSecretStore

TEST_USER_ID = "api-browser-user"
TEST_AGENT_ID = "api-browser-agent"
TEST_CHAT_ID = "api-browser-chat"


class ApiFakeBrowserProvider:
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

    async def create_profile(
        self,
        *,
        credential: BrowserProviderCredential,
        account_label: str,
        external_user_reference: str,
    ) -> OpaqueBrowserProfileHandle:
        del credential, account_label, external_user_reference
        return OpaqueBrowserProfileHandle(SecretStr("provider-profile-secret"))

    async def delete_profile(
        self,
        *,
        credential: BrowserProviderCredential,
        profile: OpaqueBrowserProfileHandle,
    ) -> None:
        del credential, profile

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
        del credential, profile, browser_country_code
        return BrowserProviderSetupSession(
            handle=OpaqueBrowserSessionHandle(SecretStr("provider-session-secret")),
            live_url="https://live.browser-use.com/setup-secret",
            expires_at=utc_now() + timedelta(minutes=10),
        )

    async def stop_session(
        self,
        *,
        credential: BrowserProviderCredential,
        session: OpaqueBrowserSessionHandle,
    ) -> None:
        del credential, session

    async def start_task(
        self,
        *,
        credential: BrowserProviderCredential,
        profile: OpaqueBrowserProfileHandle | None,
        request: BrowserTaskRequest,
    ) -> BrowserProviderTaskSession:
        del credential, profile, request
        return BrowserProviderTaskSession(
            task_handle=SecretStr("task"),
            session_handle=OpaqueBrowserSessionHandle(SecretStr("session")),
        )

    async def wait_for_task(
        self,
        *,
        credential: BrowserProviderCredential,
        task: BrowserProviderTaskSession,
    ) -> BrowserTaskResult:
        del credential, task
        return BrowserTaskResult(output_text="result", successful=True)


async def browser_user(_: Request) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=TEST_USER_ID,
        firebase_user_id="firebase-browser-user",
    )


@pytest.mark.asyncio
async def test_browser_account_api_returns_only_safe_metadata_and_no_store_capabilities(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    connection_id = await seed_api_records(
        sqlite_session_factory,
        user_secret_vault,
    )
    http_client = httpx.AsyncClient()
    app = FastAPI()
    app.include_router(
        create_browser_accounts_router(
            auth_dependency=browser_user,
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            http_client=http_client,
            settings=BrowserAccountSettings(enabled=True),
            provider=ApiFakeBrowserProvider(),
        ),
    )

    with TestClient(app) as client:
        created = client.post(
            "/api/browser-accounts",
            json={
                "label": "Billing",
                "primary_origin": "https://billing.example.com",
                "authentication_origins": ["https://login.example.com"],
                "provider_connection_id": connection_id,
                "agent_ids": [TEST_AGENT_ID],
            },
        )
        assert created.status_code == 201
        assert created.json()["browser_country_code"] == "GB"
        account_id = created.json()["id"]

        updated = client.put(
            f"/api/browser-accounts/{account_id}",
            json={
                "label": "Updated billing",
                "primary_origin": "https://billing.example.com",
                "authentication_origins": [
                    "https://auth.billing.example.com",
                ],
            },
        )
        assert updated.status_code == 200
        assert updated.json()["label"] == "Updated billing"
        assert updated.json()["authentication_origins"] == [
            "https://auth.billing.example.com",
        ]
        assert updated.json()["browser_country_code"] == "GB"

        setup = client.post(f"/api/browser-accounts/{account_id}/setup/start")
        assert setup.status_code == 200
        assert setup.headers["cache-control"] == "no-store"
        assert setup.json()["live_url"] == "https://live.browser-use.com/setup-secret"

        completed = client.post(
            f"/api/browser-accounts/{account_id}/setup/complete",
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "ready"

        approval = client.post(
            "/api/browser-accounts/authorizations",
            json={
                "account_id": account_id,
                "chat_id": TEST_CHAT_ID,
                "client_turn_id": "browser-turn-1",
                "task_summary": "Read the latest invoice total",
            },
        )
        assert approval.status_code == 200
        assert approval.headers["cache-control"] == "no-store"

        listed = client.get("/api/browser-accounts")

    await http_client.aclose()
    assert listed.status_code == 200
    serialized = listed.text
    assert "provider-profile-secret" not in serialized
    assert "provider-session-secret" not in serialized
    assert "setup-secret" not in serialized
    assert "live_url" not in serialized


@pytest.mark.asyncio
async def test_browserless_api_stores_credentials_without_returning_them(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    connection_id = await seed_browserless_records(
        sqlite_session_factory,
        user_secret_vault,
    )
    http_client = httpx.AsyncClient()
    app = FastAPI()
    app.include_router(
        create_browser_accounts_router(
            auth_dependency=browser_user,
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            http_client=http_client,
            settings=BrowserAccountSettings(enabled=True),
            provider=ApiFakeBrowserProvider(),
        ),
    )

    with TestClient(app) as client:
        missing_credentials = client.post(
            "/api/browser-accounts",
            json={
                "label": "Missing credentials",
                "primary_origin": "https://billing.example.com",
                "provider_connection_id": connection_id,
                "agent_ids": [TEST_AGENT_ID],
            },
        )
        assert missing_credentials.status_code == 422

        created = client.post(
            "/api/browser-accounts",
            json={
                "label": "Browserless billing",
                "primary_origin": "https://billing.example.com",
                "provider_connection_id": connection_id,
                "agent_ids": [TEST_AGENT_ID],
                "username": "billing@example.com",
                "password": "website-password",  # pragma: allowlist secret
            },
        )
        assert created.status_code == 201
        assert created.json()["provider"] == "browserless"
        assert created.json()["has_credentials"] is True
        assert "billing@example.com" not in created.text
        assert "website-password" not in created.text

        updated = client.put(
            f"/api/browser-accounts/{created.json()['id']}/credentials",
            json={
                "username": "new-billing@example.com",
                "password": "new-website-password",  # pragma: allowlist secret
            },
        )
        assert updated.status_code == 200
        assert updated.headers["cache-control"] == "no-store"
        assert "new-billing@example.com" not in updated.text
        assert "new-website-password" not in updated.text

        rejected_input = "sensitive-value-" + ("x" * 4096)
        rejected_create = client.post(
            "/api/browser-accounts",
            json={
                "label": "Rejected credentials",
                "primary_origin": "https://billing.example.com",
                "provider_connection_id": connection_id,
                "agent_ids": [TEST_AGENT_ID],
                "username": "billing@example.com",
                "password": rejected_input,
            },
        )
        rejected_update = client.put(
            f"/api/browser-accounts/{created.json()['id']}/credentials",
            json={
                "username": "billing@example.com",
                "password": rejected_input,
            },
        )
        assert rejected_create.status_code == 422
        assert rejected_update.status_code == 422
        assert rejected_create.headers["cache-control"] == "no-store"
        assert rejected_update.headers["cache-control"] == "no-store"
        assert rejected_input not in rejected_create.text
        assert rejected_input not in rejected_update.text

    await http_client.aclose()


@pytest.mark.asyncio
async def test_browserless_api_explains_missing_execution_tool(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    connection_id = await seed_browserless_records(
        sqlite_session_factory,
        user_secret_vault,
        allowed_tools=("browserless_search",),
    )
    http_client = httpx.AsyncClient()
    app = FastAPI()
    app.include_router(
        create_browser_accounts_router(
            auth_dependency=browser_user,
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            http_client=http_client,
            settings=BrowserAccountSettings(enabled=True),
            provider=ApiFakeBrowserProvider(),
        ),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/browser-accounts",
            json={
                "label": "Browserless billing",
                "primary_origin": "https://billing.example.com",
                "provider_connection_id": connection_id,
                "agent_ids": [TEST_AGENT_ID],
                "username": "billing@example.com",
                "password": "website-password",  # pragma: allowlist secret
            },
        )

    await http_client.aclose()
    assert response.status_code == 424
    assert response.json() == {
        "detail": ("Browserless must allow browserless_agent before it can be used for Browser Accounts"),
    }
    assert "billing@example.com" not in response.text
    assert "website-password" not in response.text


async def seed_api_records(
    session_factory: AsyncSessionFactory,
    secret_vault: UserSecretVaultService,
) -> str:
    async with session_factory() as session:
        agent = UserAgentRecord(
            id=TEST_AGENT_ID,
            owner_user_id=TEST_USER_ID,
            slug="browser-agent",
            display_name="Browser Agent",
        )
        connection = UserMcpServerRecord(
            user_id=TEST_USER_ID,
            label="Browser Use",
            url="https://api.browser-use.com/v3/mcp",
            transport="streamable_http",
            auth_type="api_key_header",
            enabled=True,
            trust_state="trusted",
            status="connected",
            allowed_tools=["run_session"],
            safe_metadata=McpServerSafeMetadata(
                preset_id="browser-use-cloud",
                preset_variant_id="api-key",
            ).model_dump(mode="json"),
        )
        session.add(agent)
        session.add(connection)
        await session.flush()
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
        await McpSecretStore(session, secret_vault).upsert_secret(
            user_id=TEST_USER_ID,
            server_id=connection.id,
            secret_kind=McpSecretKind.API_KEY,
            secret=SecretStr("provider-api-key"),
        )
        return connection.id


async def seed_browserless_records(
    session_factory: AsyncSessionFactory,
    secret_vault: UserSecretVaultService,
    *,
    allowed_tools: tuple[str, ...] = ("browserless_agent",),
) -> str:
    async with session_factory() as session:
        session.add(
            UserAgentRecord(
                id=TEST_AGENT_ID,
                owner_user_id=TEST_USER_ID,
                slug="browser-agent",
                display_name="Browser Agent",
            ),
        )
        connection = UserMcpServerRecord(
            user_id=TEST_USER_ID,
            label="Browserless",
            url="https://mcp.browserless.io/mcp",
            transport="streamable_http",
            auth_type="static_bearer",
            enabled=True,
            trust_state="trusted",
            status="connected",
            allowed_tools=list(allowed_tools),
            safe_metadata=McpServerSafeMetadata(
                preset_id="browserless",
                preset_variant_id="bearer",
            ).model_dump(mode="json"),
        )
        session.add(connection)
        await session.commit()
        await session.refresh(connection)
        await McpSecretStore(session, secret_vault).upsert_secret(
            user_id=TEST_USER_ID,
            server_id=connection.id,
            secret_kind=McpSecretKind.STATIC_BEARER,
            secret=SecretStr("browserless-provider-token"),
        )
        await session.commit()
        return connection.id

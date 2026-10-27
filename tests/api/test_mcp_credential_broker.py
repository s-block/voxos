import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from voxos.api.mcp_credential_broker import (
    BROKER_ROUTE,
    MAX_BROKER_REQUEST_BODY_BYTES,
    create_mcp_credential_broker_router,
)
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.mcp.delegation import McpCredentialDelegationSigner
from voxos.mcp.models import (
    McpOAuthClientInfo,
    McpOAuthToken,
    McpTokenEndpointAuthMethod,
)
from voxos.mcp.records import UserMcpServerRecord
from voxos.mcp.services import McpSecretStore

_USER_ID = "broker-user"
_SERVER_ID = "11111111-1111-1111-1111-111111111111"
_SIGNING_SECRET = "test-credential-broker-signing-secret"  # pragma: allowlist secret


@pytest.mark.asyncio
async def test_broker_refreshes_once_and_stale_rejection_reuses_rotation(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    refresh_requests = 0

    def refresh_handler(request: httpx.Request) -> httpx.Response:
        nonlocal refresh_requests
        refresh_requests += 1
        assert str(request.url) == "https://example.com/token"
        assert "refresh_token=old-refresh-token" in request.content.decode()
        return httpx.Response(
            200,
            json={
                "access_token": "rotated-access-token",
                "refresh_token": "rotated-refresh-token",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
            request=request,
        )

    async with sqlite_session_factory() as session:
        session.add(
            UserMcpServerRecord(
                id=_SERVER_ID,
                user_id=_USER_ID,
                label="Monzo",
                transport="streamable_http",
                url="http://monzo-mcp:8000/mcp",
                auth_type="static_bearer",
                enabled=True,
                safe_metadata={"preset_id": "monzo"},
            )
        )
        await session.commit()
        store = McpSecretStore(session, user_secret_vault)
        await store.store_oauth_client_info(
            _USER_ID,
            _SERVER_ID,
            McpOAuthClientInfo(
                client_id="client-id",
                client_secret="client-secret",  # noqa: S106  # pragma: allowlist secret
                token_endpoint="https://example.com/token",  # noqa: S106
                token_endpoint_auth_method=(McpTokenEndpointAuthMethod.CLIENT_SECRET_POST),
            ),
        )
        await store.store_oauth_token(
            _USER_ID,
            _SERVER_ID,
            McpOAuthToken(
                access_token="old-access-token",  # noqa: S106
                refresh_token="old-refresh-token",  # noqa: S106
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            ),
        )

    signer = McpCredentialDelegationSigner(
        secret=SecretStr(_SIGNING_SECRET),
    )
    assertion = signer.sign(
        user_id=_USER_ID,
        server_id=_SERVER_ID,
        provider="monzo",
    )
    app = FastAPI()
    async with httpx.AsyncClient(transport=httpx.MockTransport(refresh_handler)) as provider_client:
        app.include_router(
            create_mcp_credential_broker_router(
                session_factory=sqlite_session_factory,
                secret_vault=user_secret_vault,
                http_client=provider_client,
                delegation_signer=signer,
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://voxos.test",
            headers={"Authorization": f"Bearer {assertion}"},
        ) as client:
            refreshed = await client.post(BROKER_ROUTE, json={})
            stale_rejection = await client.post(
                BROKER_ROUTE,
                json={"rejected_access_token_sha256": hashlib.sha256(b"old-access-token").hexdigest()},
            )

    assert refreshed.status_code == 200
    assert refreshed.headers["cache-control"] == "no-store"
    assert refreshed.json()["access_token"] == "rotated-access-token"
    assert stale_rejection.status_code == 200
    assert stale_rejection.json()["access_token"] == "rotated-access-token"
    assert refresh_requests == 1


@pytest.mark.asyncio
async def test_broker_rejects_missing_or_tampered_delegation_before_database(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    signer = McpCredentialDelegationSigner(
        secret=SecretStr(_SIGNING_SECRET),
    )
    assertion = signer.sign(
        user_id=_USER_ID,
        server_id=_SERVER_ID,
        provider="monzo",
    )
    app = FastAPI()
    async with httpx.AsyncClient() as provider_client:
        app.include_router(
            create_mcp_credential_broker_router(
                session_factory=sqlite_session_factory,
                secret_vault=user_secret_vault,
                http_client=provider_client,
                delegation_signer=signer,
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://voxos.test",
        ) as client:
            missing = await client.post(BROKER_ROUTE, json={})
            tampered = await client.post(
                BROKER_ROUTE,
                json={},
                headers={"Authorization": f"Bearer {assertion}x"},
            )

    assert missing.status_code == 401
    assert tampered.status_code == 401


@pytest.mark.asyncio
async def test_broker_authenticates_before_parsing_request_body(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    async def oversized_chunks() -> AsyncIterator[bytes]:
        yield b"x" * MAX_BROKER_REQUEST_BODY_BYTES
        yield b"x"

    signer = McpCredentialDelegationSigner(
        secret=SecretStr(_SIGNING_SECRET),
    )
    assertion = signer.sign(
        user_id=_USER_ID,
        server_id=_SERVER_ID,
        provider="monzo",
    )
    app = FastAPI()
    async with httpx.AsyncClient() as provider_client:
        app.include_router(
            create_mcp_credential_broker_router(
                session_factory=sqlite_session_factory,
                secret_vault=user_secret_vault,
                http_client=provider_client,
                delegation_signer=signer,
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://voxos.test",
        ) as client:
            malformed = await client.post(
                BROKER_ROUTE,
                content=b"{",
                headers={"Content-Type": "application/json"},
            )
            oversized = await client.post(
                BROKER_ROUTE,
                content=b"x" * (MAX_BROKER_REQUEST_BODY_BYTES + 1),
                headers={"Content-Type": "application/json"},
            )
            authenticated_malformed = await client.post(
                BROKER_ROUTE,
                content=b"{",
                headers={
                    "Authorization": f"Bearer {assertion}",
                    "Content-Type": "application/json",
                },
            )
            authenticated_oversized = await client.post(
                BROKER_ROUTE,
                content=b"x" * (MAX_BROKER_REQUEST_BODY_BYTES + 1),
                headers={
                    "Authorization": f"Bearer {assertion}",
                    "Content-Type": "application/json",
                },
            )
            authenticated_chunked_oversized = await client.post(
                BROKER_ROUTE,
                content=oversized_chunks(),
                headers={
                    "Authorization": f"Bearer {assertion}",
                    "Content-Type": "application/json",
                },
            )

    assert malformed.status_code == 401
    assert oversized.status_code == 401
    assert authenticated_malformed.status_code == 422
    assert authenticated_oversized.status_code == 413
    assert authenticated_chunked_oversized.status_code == 413

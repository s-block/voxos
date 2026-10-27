from dataclasses import dataclass

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken

from voxos.auth.bearer import BearerTokenExtractor
from voxos.auth.errors import MissingAuthorizationTokenError
from voxos.auth.models import FirebaseTokenClaims, VerifiedFirebaseIdentity
from voxos.db.session import AsyncSessionFactory
from voxos.mcp.auth import (
    MCP_FIREBASE_USER_ID_CLAIM,
    MCP_LOCAL_USER_ID_CLAIM,
    AuthenticatedMcpUserResolver,
    FirebaseMcpTokenVerifier,
    McpAuthContextFactory,
    MissingMcpAccessTokenError,
    MissingMcpUserIdentityError,
    authorization_header_from_headers,
)


@dataclass(slots=True)
class StaticTokenVerifier:
    identity: VerifiedFirebaseIdentity
    seen_token: str | None = None

    async def verify(self, token: str) -> VerifiedFirebaseIdentity:
        self.seen_token = token
        return self.identity


def verified_identity(
    email: str = "user@example.com",
) -> VerifiedFirebaseIdentity:
    claims = FirebaseTokenClaims.model_validate(
        {
            "sub": "firebase-user-123",
            "aud": "project-id",
            "iss": "issuer",
            "auth_time": 1,
            "iat": 1,
            "exp": 2,
            "email": email,
            "email_verified": True,
        },
    )
    return VerifiedFirebaseIdentity.from_claims(claims)


def test_authorization_header_from_headers_is_case_insensitive() -> None:
    assert authorization_header_from_headers({"Authorization": "Bearer firebase-token"}) == "Bearer firebase-token"
    assert authorization_header_from_headers({"x-request-id": "request-123"}) is None


@pytest.mark.asyncio
async def test_mcp_auth_context_resolves_local_authenticated_user(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    verifier = StaticTokenVerifier(identity=verified_identity())
    factory = McpAuthContextFactory(
        token_extractor=BearerTokenExtractor(),
        token_verifier=verifier,
        session_factory=sqlite_session_factory,
    )

    context = await factory.from_authorization_header("Bearer firebase-token")

    assert context.user_id != "firebase-user-123"
    assert context.authenticated_user.user_id == context.user_id
    assert context.authenticated_user.firebase_user_id == "firebase-user-123"
    assert verifier.seen_token == "firebase-token"


@pytest.mark.asyncio
async def test_mcp_auth_context_reuses_local_user_id(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    first_factory = McpAuthContextFactory(
        token_extractor=BearerTokenExtractor(),
        token_verifier=StaticTokenVerifier(identity=verified_identity()),
        session_factory=sqlite_session_factory,
    )
    second_factory = McpAuthContextFactory(
        token_extractor=BearerTokenExtractor(),
        token_verifier=StaticTokenVerifier(
            identity=verified_identity(email="next@example.com"),
        ),
        session_factory=sqlite_session_factory,
    )

    first_context = await first_factory.from_headers(
        {"Authorization": "Bearer firebase-token"},
    )
    second_context = await second_factory.from_headers(
        {"authorization": "Bearer firebase-token"},
    )

    assert second_context.user_id == first_context.user_id
    assert str(second_context.authenticated_user.email) == "next@example.com"


@pytest.mark.asyncio
async def test_mcp_auth_context_rejects_missing_token(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    factory = McpAuthContextFactory(
        token_extractor=BearerTokenExtractor(),
        token_verifier=StaticTokenVerifier(identity=verified_identity()),
        session_factory=sqlite_session_factory,
    )

    with pytest.raises(MissingAuthorizationTokenError):
        await factory.from_authorization_header(None)


@pytest.mark.asyncio
async def test_firebase_mcp_token_verifier_returns_local_user_subject(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    firebase_verifier = StaticTokenVerifier(identity=verified_identity())
    auth_context_factory = McpAuthContextFactory(
        token_extractor=BearerTokenExtractor(),
        token_verifier=firebase_verifier,
        session_factory=sqlite_session_factory,
    )
    verifier = FirebaseMcpTokenVerifier(auth_context_factory)

    access_token = await verifier.verify_token("firebase-token")

    assert access_token is not None
    assert access_token.token == "firebase-token"
    assert access_token.client_id == "firebase-user-123"
    assert access_token.subject is not None
    assert access_token.subject != "firebase-user-123"
    assert access_token.claims is not None
    assert access_token.claims[MCP_LOCAL_USER_ID_CLAIM] == access_token.subject
    assert access_token.claims[MCP_FIREBASE_USER_ID_CLAIM] == "firebase-user-123"


def test_authenticated_mcp_user_resolver_rejects_missing_access_token() -> None:
    resolver = AuthenticatedMcpUserResolver()

    with pytest.raises(MissingMcpAccessTokenError):
        resolver.require_user_id()


def test_authenticated_mcp_user_resolver_uses_local_user_claim() -> None:
    bearer_value = "redacted"
    access_token = AccessToken(
        token=bearer_value,
        client_id="firebase-user-123",
        scopes=[],
        subject="fallback-user",
        claims={MCP_LOCAL_USER_ID_CLAIM: " local-user-123 "},
    )
    context_token = auth_context_var.set(AuthenticatedUser(access_token))
    try:
        user_id = AuthenticatedMcpUserResolver().require_user_id()
    finally:
        auth_context_var.reset(context_token)

    assert user_id == "local-user-123"


def test_authenticated_mcp_user_resolver_rejects_subject_only_token_by_default() -> None:
    bearer_value = "redacted"
    access_token = AccessToken(
        token=bearer_value,
        client_id="firebase-user-123",
        scopes=[],
        subject="subject-user-123",
        claims={},
    )
    context_token = auth_context_var.set(AuthenticatedUser(access_token))
    try:
        with pytest.raises(MissingMcpUserIdentityError):
            AuthenticatedMcpUserResolver().require_user_id()
    finally:
        auth_context_var.reset(context_token)


def test_authenticated_mcp_user_resolver_allows_subject_fallback_when_configured() -> None:
    bearer_value = "redacted"
    access_token = AccessToken(
        token=bearer_value,
        client_id="firebase-user-123",
        scopes=[],
        subject=" subject-user-123 ",
        claims={},
    )
    context_token = auth_context_var.set(AuthenticatedUser(access_token))
    try:
        user_id = AuthenticatedMcpUserResolver(
            require_local_user_claim=False,
        ).require_user_id()
    finally:
        auth_context_var.reset(context_token)

    assert user_id == "subject-user-123"

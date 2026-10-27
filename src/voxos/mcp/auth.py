import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import httpx
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl
from sqlalchemy.exc import SQLAlchemyError

from voxos.auth.bearer import BearerTokenExtractor
from voxos.auth.factory import FirebaseAuthFactory
from voxos.auth.models import AuthenticatedUser, VerifiedFirebaseIdentity
from voxos.auth.protocols import FirebaseIdentityVerifier
from voxos.auth.services import FirebaseUserService
from voxos.auth.settings import FirebaseAuthSettings
from voxos.db.session import AsyncSessionFactory

logger = logging.getLogger(__name__)

AUTHORIZATION_HEADER_NAME = "authorization"
MCP_LOCAL_USER_ID_CLAIM = "voxos_user_id"
MCP_FIREBASE_USER_ID_CLAIM = "firebase_user_id"


class McpAuthenticationError(Exception):
    """Base exception for MCP authentication and user resolution failures."""


class MissingMcpAccessTokenError(McpAuthenticationError):
    def __init__(self) -> None:
        super().__init__("MCP access token is missing")


class MissingMcpUserIdentityError(McpAuthenticationError):
    def __init__(self) -> None:
        super().__init__("MCP access token does not identify a Voxos user")


class McpTransportAuthConfigurationError(ValueError):
    """Raised when an exposed MCP server is configured without transport auth."""


@dataclass(frozen=True, slots=True)
class McpTransportAuthConfig:
    token_verifier: TokenVerifier
    issuer_url: str
    resource_server_url: str

    def __post_init__(self) -> None:
        if not callable(getattr(self.token_verifier, "verify_token", None)):
            message = "token_verifier is required for exposed MCP servers"
            raise McpTransportAuthConfigurationError(message)
        if (
            not isinstance(self.issuer_url, str)
            or self.issuer_url.strip() == ""
            or not isinstance(self.resource_server_url, str)
            or self.resource_server_url.strip() == ""
        ):
            message = "issuer_url and resource_server_url are required for MCP auth"
            raise McpTransportAuthConfigurationError(message)

    def auth_settings(self) -> AuthSettings:
        return AuthSettings(
            issuer_url=AnyHttpUrl(self.issuer_url),
            resource_server_url=AnyHttpUrl(self.resource_server_url),
            # FirebaseMcpTokenVerifier validates the Firebase project audience.
            validate_token_resource=False,
        )


class McpUserResolver(Protocol):
    def require_user_id(self) -> str:
        """Return the authenticated local Voxos user ID for this MCP request."""


@dataclass(frozen=True, slots=True)
class McpAuthenticatedContext:
    authenticated_user: AuthenticatedUser

    @property
    def user_id(self) -> str:
        return self.authenticated_user.user_id


@dataclass(frozen=True, slots=True)
class McpAuthContextFactory:
    token_extractor: BearerTokenExtractor
    token_verifier: FirebaseIdentityVerifier
    session_factory: AsyncSessionFactory
    last_seen_update_interval_seconds: int = 300

    async def from_authorization_header(
        self,
        authorization_header: str | None,
    ) -> McpAuthenticatedContext:
        token = self.token_extractor.extract(authorization_header)
        identity = await self.token_verifier.verify(token)
        authenticated_user = await self._resolve_user(identity)
        return McpAuthenticatedContext(authenticated_user=authenticated_user)

    async def from_headers(
        self,
        headers: Mapping[str, str],
    ) -> McpAuthenticatedContext:
        return await self.from_authorization_header(
            authorization_header=authorization_header_from_headers(headers),
        )

    async def _resolve_user(
        self,
        identity: VerifiedFirebaseIdentity,
    ) -> AuthenticatedUser:
        try:
            async with self.session_factory() as session:
                service = FirebaseUserService(session)
                record = await service.upsert_from_verified_identity(
                    identity=identity,
                    last_seen_update_interval_seconds=(self.last_seen_update_interval_seconds),
                )
                return AuthenticatedUser.from_verified_identity(
                    user_id=record.id,
                    identity=identity,
                )
        except SQLAlchemyError:
            logger.exception(
                "failed to resolve MCP authenticated user: firebase_user_id=%s",
                identity.firebase_user_id,
            )
            raise


@dataclass(frozen=True, slots=True)
class FirebaseMcpTokenVerifier:
    auth_context_factory: McpAuthContextFactory

    async def verify_token(self, token: str) -> AccessToken | None:
        context = await self.auth_context_factory.from_authorization_header(
            f"Bearer {token}",
        )
        user = context.authenticated_user
        return AccessToken(
            token=token,
            client_id=user.firebase_user_id,
            scopes=[],
            subject=user.user_id,
            claims={
                MCP_LOCAL_USER_ID_CLAIM: user.user_id,
                MCP_FIREBASE_USER_ID_CLAIM: user.firebase_user_id,
            },
        )


@dataclass(frozen=True, slots=True)
class AuthenticatedMcpUserResolver:
    require_local_user_claim: bool = True

    def require_user_id(self) -> str:
        access_token = get_access_token()
        if access_token is None:
            raise MissingMcpAccessTokenError

        user_id = self._user_id_from_access_token(access_token)
        if user_id is None:
            raise MissingMcpUserIdentityError

        return user_id

    def _user_id_from_access_token(
        self,
        access_token: AccessToken,
    ) -> str | None:
        claim_user_id = self._claim_user_id(access_token)
        if claim_user_id is not None:
            return claim_user_id

        if self.require_local_user_claim:
            return None

        if access_token.subject is None:
            return None

        stripped_subject = access_token.subject.strip()
        return stripped_subject or None

    def _claim_user_id(self, access_token: AccessToken) -> str | None:
        claims = access_token.claims
        if claims is None:
            return None

        claim_value = claims.get(MCP_LOCAL_USER_ID_CLAIM)
        if not isinstance(claim_value, str):
            return None

        stripped_claim = claim_value.strip()
        return stripped_claim or None


def authorization_header_from_headers(headers: Mapping[str, str]) -> str | None:
    for name, value in headers.items():
        if name.lower() == AUTHORIZATION_HEADER_NAME:
            return value
    return None


def create_firebase_mcp_auth_context_factory(
    settings: FirebaseAuthSettings,
    session_factory: AsyncSessionFactory,
    http_client: httpx.AsyncClient | None = None,
) -> McpAuthContextFactory:
    auth_factory = FirebaseAuthFactory(
        settings=settings,
        session_factory=session_factory,
        http_client=http_client,
    )
    return McpAuthContextFactory(
        token_extractor=BearerTokenExtractor(),
        token_verifier=auth_factory.create_token_verifier(),
        session_factory=session_factory,
        last_seen_update_interval_seconds=(settings.firebase_auth_last_seen_update_interval_seconds),
    )

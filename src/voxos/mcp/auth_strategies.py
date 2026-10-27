from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from pydantic import SecretStr

from voxos.mcp.errors import McpAuthRequiredError
from voxos.mcp.models import (
    McpAuthSummary,
    McpAuthType,
    McpOAuthClientInfo,
    McpOAuthToken,
    McpSecretKind,
    McpServerAuthConfig,
)
from voxos.mcp.oauth import McpOAuthHttpClient
from voxos.mcp.records import UserMcpServerRecord

AUTHORIZATION_HEADER_NAME = "Authorization"
TOKEN_EXPIRY_SKEW = timedelta(seconds=60)
OAUTH_AUTH_ERROR_SERVER_ID = "oauth"


@dataclass(frozen=True, slots=True)
class McpConnectionAuth:
    headers: dict[str, str]


@dataclass(frozen=True, slots=True)
class McpPrivateKeyMaterial:
    private_key: SecretStr
    algorithm: str


class McpAuthSecretStore(Protocol):
    async def load_secret_value(
        self,
        user_id: str,
        server_id: str,
        secret_kind: McpSecretKind,
    ) -> SecretStr | None:
        """Return a decrypted secret value."""

    async def load_secret_summary(
        self,
        user_id: str,
        server_id: str,
        secret_kind: McpSecretKind,
    ) -> McpAuthSummary:
        """Return a safe summary for a secret."""

    async def load_oauth_token(
        self,
        user_id: str,
        server_id: str,
    ) -> McpOAuthToken | None:
        """Return a decrypted OAuth token."""

    async def store_oauth_token(
        self,
        user_id: str,
        server_id: str,
        token: McpOAuthToken,
    ) -> None:
        """Encrypt and store an OAuth token."""

    async def load_oauth_client_info(
        self,
        user_id: str,
        server_id: str,
    ) -> McpOAuthClientInfo | None:
        """Return decrypted OAuth client information."""

    async def load_private_key_material(
        self,
        user_id: str,
        server_id: str,
    ) -> McpPrivateKeyMaterial | None:
        """Return private-key JWT signing material."""

    async def delete_auth_for_server(self, user_id: str, server_id: str) -> None:
        """Delete stored auth material for a server."""


@dataclass(frozen=True, slots=True)
class McpAuthStrategyDependencies:
    secret_store: McpAuthSecretStore
    oauth_client: McpOAuthHttpClient


class McpAuthStrategy(Protocol):
    async def auth_for_connection(
        self,
        server: UserMcpServerRecord,
        dependencies: McpAuthStrategyDependencies,
    ) -> McpConnectionAuth:
        """Return HTTP headers for connecting to the server."""

    async def status(
        self,
        server: UserMcpServerRecord,
        dependencies: McpAuthStrategyDependencies,
    ) -> McpAuthSummary:
        """Return safe auth status for the server."""

    async def revoke(
        self,
        server: UserMcpServerRecord,
        dependencies: McpAuthStrategyDependencies,
    ) -> None:
        """Revoke/delete auth material for the server."""


class NoAuthStrategy:
    async def auth_for_connection(
        self,
        server: UserMcpServerRecord,
        dependencies: McpAuthStrategyDependencies,
    ) -> McpConnectionAuth:
        header_name = auth_config_from_record(server).api_key_header_name
        if header_name is None:
            return McpConnectionAuth(headers={})
        secret = await dependencies.secret_store.load_secret_value(
            user_id=server.user_id,
            server_id=server.id,
            secret_kind=McpSecretKind.API_KEY,
        )
        if secret is None:
            return McpConnectionAuth(headers={})
        api_key = secret.get_secret_value().strip()
        return McpConnectionAuth(
            headers={} if not api_key else {header_name: api_key},
        )

    async def status(
        self,
        server: UserMcpServerRecord,
        dependencies: McpAuthStrategyDependencies,
    ) -> McpAuthSummary:
        return McpAuthSummary(authenticated=True, needs_reauth=False)

    async def revoke(
        self,
        server: UserMcpServerRecord,
        dependencies: McpAuthStrategyDependencies,
    ) -> None:
        await dependencies.secret_store.delete_auth_for_server(
            user_id=server.user_id,
            server_id=server.id,
        )


class StaticBearerStrategy:
    async def auth_for_connection(
        self,
        server: UserMcpServerRecord,
        dependencies: McpAuthStrategyDependencies,
    ) -> McpConnectionAuth:
        secret = await dependencies.secret_store.load_secret_value(
            user_id=server.user_id,
            server_id=server.id,
            secret_kind=McpSecretKind.STATIC_BEARER,
        )
        if secret is None:
            raise McpAuthRequiredError(server.id)
        token = secret.get_secret_value().strip()
        if not token:
            raise McpAuthRequiredError(server.id)
        return McpConnectionAuth(
            headers={AUTHORIZATION_HEADER_NAME: f"Bearer {token}"},
        )

    async def status(
        self,
        server: UserMcpServerRecord,
        dependencies: McpAuthStrategyDependencies,
    ) -> McpAuthSummary:
        return await dependencies.secret_store.load_secret_summary(
            user_id=server.user_id,
            server_id=server.id,
            secret_kind=McpSecretKind.STATIC_BEARER,
        )

    async def revoke(
        self,
        server: UserMcpServerRecord,
        dependencies: McpAuthStrategyDependencies,
    ) -> None:
        await dependencies.secret_store.delete_auth_for_server(server.user_id, server.id)


class ApiKeyHeaderStrategy:
    async def auth_for_connection(
        self,
        server: UserMcpServerRecord,
        dependencies: McpAuthStrategyDependencies,
    ) -> McpConnectionAuth:
        secret = await dependencies.secret_store.load_secret_value(
            user_id=server.user_id,
            server_id=server.id,
            secret_kind=McpSecretKind.API_KEY,
        )
        config = auth_config_from_record(server)
        if secret is None or config.api_key_header_name is None:
            raise McpAuthRequiredError(server.id)
        api_key = secret.get_secret_value().strip()
        if not api_key:
            raise McpAuthRequiredError(server.id)
        return McpConnectionAuth(headers={config.api_key_header_name: api_key})

    async def status(
        self,
        server: UserMcpServerRecord,
        dependencies: McpAuthStrategyDependencies,
    ) -> McpAuthSummary:
        return await dependencies.secret_store.load_secret_summary(
            user_id=server.user_id,
            server_id=server.id,
            secret_kind=McpSecretKind.API_KEY,
        )

    async def revoke(
        self,
        server: UserMcpServerRecord,
        dependencies: McpAuthStrategyDependencies,
    ) -> None:
        await dependencies.secret_store.delete_auth_for_server(server.user_id, server.id)


class OAuthAuthorizationCodeStrategy:
    async def auth_for_connection(
        self,
        server: UserMcpServerRecord,
        dependencies: McpAuthStrategyDependencies,
    ) -> McpConnectionAuth:
        token = await token_with_refresh(server, dependencies, allow_client_grant=False)
        return bearer_auth(token)

    async def status(
        self,
        server: UserMcpServerRecord,
        dependencies: McpAuthStrategyDependencies,
    ) -> McpAuthSummary:
        token = await dependencies.secret_store.load_oauth_token(server.user_id, server.id)
        if token is None:
            return McpAuthSummary(authenticated=False, needs_reauth=True)
        return McpAuthSummary(
            authenticated=True,
            needs_reauth=token_expired(token) and token.refresh_token is None,
            expires_at=token.expires_at,
            scopes=token.scopes,
        )

    async def revoke(
        self,
        server: UserMcpServerRecord,
        dependencies: McpAuthStrategyDependencies,
    ) -> None:
        await dependencies.secret_store.delete_auth_for_server(server.user_id, server.id)


class OAuthClientCredentialsStrategy:
    async def auth_for_connection(
        self,
        server: UserMcpServerRecord,
        dependencies: McpAuthStrategyDependencies,
    ) -> McpConnectionAuth:
        token = await token_with_refresh(server, dependencies, allow_client_grant=True)
        return bearer_auth(token)

    async def status(
        self,
        server: UserMcpServerRecord,
        dependencies: McpAuthStrategyDependencies,
    ) -> McpAuthSummary:
        client_info = await dependencies.secret_store.load_oauth_client_info(
            server.user_id,
            server.id,
        )
        token = await dependencies.secret_store.load_oauth_token(server.user_id, server.id)
        if client_info is None:
            return McpAuthSummary(authenticated=False, needs_reauth=True)
        return McpAuthSummary(
            authenticated=True,
            needs_reauth=False,
            expires_at=None if token is None else token.expires_at,
            scopes=client_info.scopes if token is None else token.scopes,
        )

    async def revoke(
        self,
        server: UserMcpServerRecord,
        dependencies: McpAuthStrategyDependencies,
    ) -> None:
        await dependencies.secret_store.delete_auth_for_server(server.user_id, server.id)


class PrivateKeyJwtStrategy:
    async def auth_for_connection(
        self,
        server: UserMcpServerRecord,
        dependencies: McpAuthStrategyDependencies,
    ) -> McpConnectionAuth:
        token = await token_with_private_key_jwt(server, dependencies)
        return bearer_auth(token)

    async def status(
        self,
        server: UserMcpServerRecord,
        dependencies: McpAuthStrategyDependencies,
    ) -> McpAuthSummary:
        client_info = await dependencies.secret_store.load_oauth_client_info(
            server.user_id,
            server.id,
        )
        private_key = await dependencies.secret_store.load_private_key_material(
            server.user_id,
            server.id,
        )
        token = await dependencies.secret_store.load_oauth_token(server.user_id, server.id)
        return McpAuthSummary(
            authenticated=client_info is not None and private_key is not None,
            needs_reauth=client_info is None or private_key is None,
            expires_at=None if token is None else token.expires_at,
            scopes=() if client_info is None else client_info.scopes,
        )

    async def revoke(
        self,
        server: UserMcpServerRecord,
        dependencies: McpAuthStrategyDependencies,
    ) -> None:
        await dependencies.secret_store.delete_auth_for_server(server.user_id, server.id)


def strategy_for_auth_type(auth_type: McpAuthType) -> McpAuthStrategy:
    if auth_type == McpAuthType.NONE:
        return NoAuthStrategy()
    if auth_type == McpAuthType.STATIC_BEARER:
        return StaticBearerStrategy()
    if auth_type == McpAuthType.API_KEY_HEADER:
        return ApiKeyHeaderStrategy()
    if auth_type == McpAuthType.OAUTH_AUTHORIZATION_CODE:
        return OAuthAuthorizationCodeStrategy()
    if auth_type == McpAuthType.OAUTH_CLIENT_CREDENTIALS:
        return OAuthClientCredentialsStrategy()
    return PrivateKeyJwtStrategy()


async def token_with_refresh(
    server: UserMcpServerRecord,
    dependencies: McpAuthStrategyDependencies,
    allow_client_grant: bool,
) -> McpOAuthToken:
    token = await dependencies.secret_store.load_oauth_token(server.user_id, server.id)
    if token is not None and not token_expired(token):
        return token

    client_info = await dependencies.secret_store.load_oauth_client_info(
        server.user_id,
        server.id,
    )
    if client_info is None:
        raise McpAuthRequiredError(server.id)

    refreshed_token: McpOAuthToken | None = None
    if token is not None and token.refresh_token is not None:
        refreshed_token = await dependencies.oauth_client.refresh_token(
            client_info=client_info,
            token=token,
        )
    elif allow_client_grant:
        refreshed_token = await dependencies.oauth_client.client_credentials_token_for_client(
            client_info,
        )

    if refreshed_token is None:
        raise McpAuthRequiredError(server.id)

    await dependencies.secret_store.store_oauth_token(
        user_id=server.user_id,
        server_id=server.id,
        token=refreshed_token,
    )
    return refreshed_token


async def token_with_private_key_jwt(
    server: UserMcpServerRecord,
    dependencies: McpAuthStrategyDependencies,
) -> McpOAuthToken:
    token = await dependencies.secret_store.load_oauth_token(server.user_id, server.id)
    if token is not None and not token_expired(token):
        return token

    client_info = await dependencies.secret_store.load_oauth_client_info(
        server.user_id,
        server.id,
    )
    private_key = await dependencies.secret_store.load_private_key_material(
        server.user_id,
        server.id,
    )
    if client_info is None or private_key is None:
        raise McpAuthRequiredError(server.id)

    refreshed_token = await dependencies.oauth_client.private_key_jwt_token_for_client(
        client_info=client_info,
        private_key=private_key.private_key,
        algorithm=private_key.algorithm,
    )
    await dependencies.secret_store.store_oauth_token(
        user_id=server.user_id,
        server_id=server.id,
        token=refreshed_token,
    )
    return refreshed_token


def bearer_auth(token: McpOAuthToken) -> McpConnectionAuth:
    if token.token_type.lower() != "bearer":
        raise McpAuthRequiredError(OAUTH_AUTH_ERROR_SERVER_ID)
    return McpConnectionAuth(
        headers={AUTHORIZATION_HEADER_NAME: f"Bearer {token.access_token}"},
    )


def token_expired(token: McpOAuthToken) -> bool:
    if token.expires_at is None:
        return False
    return token.expires_at <= datetime.now(UTC) + TOKEN_EXPIRY_SKEW


def auth_config_from_record(record: UserMcpServerRecord) -> McpServerAuthConfig:
    return McpServerAuthConfig.model_validate(record.auth_config)

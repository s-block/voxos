import base64
import hashlib
import re
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urljoin, urlparse, urlunparse

import httpx
import jwt
import orjson
from pydantic import SecretStr

from voxos.mcp.errors import McpOAuthConfigurationError
from voxos.mcp.models import (
    McpClientCredentialsAuthRequest,
    McpOAuthClientInfo,
    McpOAuthStartRequest,
    McpOAuthToken,
    McpPrivateKeyJwtAuthRequest,
    McpTokenEndpointAuthMethod,
)
from voxos.mcp.url_policy import McpOutboundUrlPolicy, validated_mcp_outbound_url

OAUTH_METADATA_PATH = "/.well-known/oauth-authorization-server"
OPENID_METADATA_PATH = "/.well-known/openid-configuration"
PROTECTED_RESOURCE_METADATA_PATH = "/.well-known/oauth-protected-resource"
PKCE_VERIFIER_BYTES = 48
STATE_BYTES = 32
REQUEST_TIMEOUT_SECONDS = 10.0
PRIVATE_KEY_ASSERTION_TTL_SECONDS = 300
MISSING_REFRESH_TOKEN_MESSAGE = "OAuth refresh token is missing"
MISSING_CLIENT_ID_MESSAGE = "OAuth client_id is required when dynamic registration is unavailable"
JSON_OBJECT_RESPONSE_MESSAGE = "OAuth response must be a JSON object"
GOOGLE_AUTHORIZATION_HOST = "accounts.google.com"
RESOURCE_METADATA_PATTERN = re.compile(
    r'resource_metadata=(?:"([^"]+)"|([^,\s]+))',
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class McpPreparedAuthorization:
    authorization_url: str
    client_info: McpOAuthClientInfo
    state: str
    code_verifier: str | None


@dataclass(frozen=True, slots=True)
class McpOAuthEndpoints:
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None = None
    resource: str | None = None
    resource_parameter_supported: bool = False


class McpOAuthHttpClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        url_policy: McpOutboundUrlPolicy | None = None,
    ) -> None:
        self._http_client = http_client
        self._url_policy = url_policy or McpOutboundUrlPolicy()

    async def prepare_authorization(
        self,
        server_url: str,
        request: McpOAuthStartRequest,
        state: str | None = None,
        use_pkce: bool = True,
    ) -> McpPreparedAuthorization:
        endpoints = await self._resolve_authorization_endpoints(
            server_url=server_url,
            request=request,
        )
        client_info = await self._resolve_authorization_client(
            request=request,
            endpoints=endpoints,
        )
        resolved_state = state or secrets.token_urlsafe(STATE_BYTES)
        code_verifier = secrets.token_urlsafe(PKCE_VERIFIER_BYTES) if use_pkce else None
        authorization_params = {
            "response_type": "code",
            "client_id": client_info.client_id,
            "redirect_uri": request.redirect_uri,
            "state": resolved_state,
        }
        if code_verifier is not None:
            authorization_params["code_challenge"] = pkce_challenge(code_verifier)
            authorization_params["code_challenge_method"] = "S256"
        scope = scope_text(request.scopes)
        if scope:
            authorization_params["scope"] = scope
        if client_info.resource is not None:
            authorization_params["resource"] = client_info.resource
        authorization_params.update(
            provider_authorization_parameters(endpoints.authorization_endpoint),
        )

        return McpPreparedAuthorization(
            authorization_url=(f"{endpoints.authorization_endpoint}?{urlencode(authorization_params)}"),
            client_info=client_info,
            state=resolved_state,
            code_verifier=code_verifier,
        )

    async def exchange_authorization_code(
        self,
        client_info: McpOAuthClientInfo,
        code: str,
        redirect_uri: str,
        code_verifier: str | None,
    ) -> McpOAuthToken:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_info.client_id,
        }
        if code_verifier is not None:
            data["code_verifier"] = code_verifier
        add_resource_parameter(data, client_info)
        return await self._request_token(client_info=client_info, data=data)

    async def refresh_token(
        self,
        client_info: McpOAuthClientInfo,
        token: McpOAuthToken,
    ) -> McpOAuthToken:
        if token.refresh_token is None:
            raise McpOAuthConfigurationError(MISSING_REFRESH_TOKEN_MESSAGE)

        data = {
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
            "client_id": client_info.client_id,
        }
        add_resource_parameter(data, client_info)
        refreshed_token = await self._request_token(client_info=client_info, data=data)
        if refreshed_token.refresh_token is not None:
            return refreshed_token
        return refreshed_token.model_copy(
            update={"refresh_token": token.refresh_token},
        )

    async def client_credentials_token(
        self,
        request: McpClientCredentialsAuthRequest,
    ) -> tuple[McpOAuthClientInfo, McpOAuthToken]:
        client_info = McpOAuthClientInfo(
            client_id=request.client_id,
            client_secret=request.client_secret.get_secret_value(),
            token_endpoint=request.token_endpoint,
            token_endpoint_auth_method=McpTokenEndpointAuthMethod.CLIENT_SECRET_BASIC,
            scopes=request.scopes,
        )
        token = await self.client_credentials_token_for_client(client_info)
        return client_info, token

    async def client_credentials_token_for_client(
        self,
        client_info: McpOAuthClientInfo,
    ) -> McpOAuthToken:
        data = {
            "grant_type": "client_credentials",
            "client_id": client_info.client_id,
        }
        scope = scope_text(client_info.scopes)
        if scope:
            data["scope"] = scope
        return await self._request_token(client_info=client_info, data=data)

    async def private_key_jwt_token(
        self,
        request: McpPrivateKeyJwtAuthRequest,
    ) -> tuple[McpOAuthClientInfo, McpOAuthToken]:
        client_info = McpOAuthClientInfo(
            client_id=request.client_id,
            token_endpoint=request.token_endpoint,
            token_endpoint_auth_method=McpTokenEndpointAuthMethod.PRIVATE_KEY_JWT,
            scopes=request.scopes,
        )
        token = await self.private_key_jwt_token_for_client(
            client_info=client_info,
            private_key=request.private_key,
            algorithm=request.algorithm,
        )
        return client_info, token

    async def private_key_jwt_token_for_client(
        self,
        client_info: McpOAuthClientInfo,
        private_key: SecretStr,
        algorithm: str = "RS256",
    ) -> McpOAuthToken:
        now = int(time.time())
        assertion = jwt.encode(
            {
                "iss": client_info.client_id,
                "sub": client_info.client_id,
                "aud": client_info.token_endpoint,
                "jti": secrets.token_urlsafe(24),
                "iat": now,
                "exp": now + PRIVATE_KEY_ASSERTION_TTL_SECONDS,
            },
            private_key.get_secret_value(),
            algorithm=algorithm,
        )
        data = {
            "grant_type": "client_credentials",
            "client_id": client_info.client_id,
            "client_assertion_type": ("urn:ietf:params:oauth:client-assertion-type:jwt-bearer"),
            "client_assertion": assertion,
        }
        scope = scope_text(client_info.scopes)
        if scope:
            data["scope"] = scope
        return await self._request_token(client_info=client_info, data=data)

    async def _resolve_authorization_client(
        self,
        request: McpOAuthStartRequest,
        endpoints: McpOAuthEndpoints,
    ) -> McpOAuthClientInfo:
        method = resolved_auth_method(
            requested_method=request.token_endpoint_auth_method,
            client_secret=request.client_secret,
        )
        if request.client_id is not None:
            return McpOAuthClientInfo(
                client_id=request.client_id,
                client_secret=secret_value(request.client_secret),
                token_endpoint=endpoints.token_endpoint,
                authorization_endpoint=endpoints.authorization_endpoint,
                registration_endpoint=endpoints.registration_endpoint,
                token_endpoint_auth_method=method,
                scopes=request.scopes,
                resource=endpoints.resource if endpoints.resource_parameter_supported else None,
            )

        if endpoints.registration_endpoint is None:
            raise McpOAuthConfigurationError(MISSING_CLIENT_ID_MESSAGE)

        await self._url_policy.validate_url(endpoints.registration_endpoint)
        registration_response = await self._http_client.post(
            endpoints.registration_endpoint,
            json={
                "client_name": request.client_name,
                "redirect_uris": [request.redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": method.value,
                "scope": scope_text(request.scopes),
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if registration_response.status_code >= 400:
            message = f"OAuth dynamic client registration failed with {registration_response.status_code}"
            raise McpOAuthConfigurationError(message)

        payload = object_mapping(registration_response.content)
        client_id = required_string(payload, "client_id")
        client_secret = optional_string(payload, "client_secret")
        response_method = optional_string(payload, "token_endpoint_auth_method")
        if response_method is not None:
            method = McpTokenEndpointAuthMethod(response_method)
        return McpOAuthClientInfo(
            client_id=client_id,
            client_secret=client_secret,
            token_endpoint=endpoints.token_endpoint,
            authorization_endpoint=endpoints.authorization_endpoint,
            registration_endpoint=endpoints.registration_endpoint,
            token_endpoint_auth_method=method,
            scopes=request.scopes,
            resource=endpoints.resource if endpoints.resource_parameter_supported else None,
        )

    async def _resolve_authorization_endpoints(
        self,
        server_url: str,
        request: McpOAuthStartRequest,
    ) -> McpOAuthEndpoints:
        if request.authorization_endpoint is not None and request.token_endpoint is not None:
            return McpOAuthEndpoints(
                authorization_endpoint=validated_mcp_outbound_url(request.authorization_endpoint),
                token_endpoint=validated_mcp_outbound_url(request.token_endpoint),
                registration_endpoint=(
                    None
                    if request.registration_endpoint is None
                    else validated_mcp_outbound_url(request.registration_endpoint)
                ),
            )

        discovered = await self._discover_endpoints(server_url)
        authorization_endpoint = (
            request.authorization_endpoint
            or discovered.authorization_endpoint
            or urljoin(origin_from_url(server_url), "/authorize")
        )
        token_endpoint = (
            request.token_endpoint or discovered.token_endpoint or urljoin(origin_from_url(server_url), "/token")
        )
        registration_endpoint = request.registration_endpoint or discovered.registration_endpoint
        return McpOAuthEndpoints(
            authorization_endpoint=validated_mcp_outbound_url(authorization_endpoint),
            token_endpoint=validated_mcp_outbound_url(token_endpoint),
            registration_endpoint=(
                None if registration_endpoint is None else validated_mcp_outbound_url(registration_endpoint)
            ),
            resource=discovered.resource,
            resource_parameter_supported=discovered.resource_parameter_supported,
        )

    async def _discover_endpoints(self, server_url: str) -> McpOAuthEndpoints:
        protected_resource_endpoints = await self._discover_protected_resource_endpoints(
            server_url,
        )
        if has_authorization_endpoints(protected_resource_endpoints):
            return protected_resource_endpoints

        origin = origin_from_url(server_url)
        discovered = await self._discover_authorization_server_metadata(
            oauth_metadata_urls_for_issuer(origin),
            raise_on_http_error=True,
        )
        if has_authorization_endpoints(discovered):
            return discovered

        return McpOAuthEndpoints(
            authorization_endpoint="",
            token_endpoint="",
        )

    async def _discover_protected_resource_endpoints(
        self,
        server_url: str,
    ) -> McpOAuthEndpoints:
        resource_metadata_urls = await self._protected_resource_metadata_urls(server_url)
        for metadata_url in resource_metadata_urls:
            resource_metadata = await self._fetch_metadata(metadata_url)
            if resource_metadata is None:
                continue
            resource = optional_string(resource_metadata, "resource")
            authorization_servers = string_sequence(
                resource_metadata,
                "authorization_servers",
            )
            for issuer in authorization_servers:
                discovered = await self._discover_authorization_server_metadata(
                    oauth_metadata_urls_for_issuer(issuer),
                    raise_on_http_error=False,
                )
                if has_authorization_endpoints(discovered):
                    return McpOAuthEndpoints(
                        authorization_endpoint=discovered.authorization_endpoint,
                        token_endpoint=discovered.token_endpoint,
                        registration_endpoint=discovered.registration_endpoint,
                        resource=resource,
                        resource_parameter_supported=discovered.resource_parameter_supported,
                    )
        return McpOAuthEndpoints(
            authorization_endpoint="",
            token_endpoint="",
        )

    async def _protected_resource_metadata_urls(
        self,
        server_url: str,
    ) -> tuple[str, ...]:
        candidates = list(protected_resource_metadata_urls_for_resource(server_url))
        challenge_url = await self._resource_metadata_url_from_challenge(server_url)
        if challenge_url is not None:
            candidates.insert(0, challenge_url)
        return tuple(dict.fromkeys(candidates))

    async def _resource_metadata_url_from_challenge(
        self,
        server_url: str,
    ) -> str | None:
        try:
            await self._url_policy.validate_url(server_url)
            response = await self._http_client.get(
                server_url,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError:
            return None
        header_value = response.headers.get("WWW-Authenticate")
        if header_value is None:
            return None
        metadata_url = resource_metadata_url_from_www_authenticate(header_value)
        if metadata_url is None:
            return None
        try:
            return validated_mcp_outbound_url(metadata_url)
        except ValueError:
            return None

    async def _discover_authorization_server_metadata(
        self,
        metadata_urls: tuple[str, ...],
        *,
        raise_on_http_error: bool,
    ) -> McpOAuthEndpoints:
        for metadata_url in metadata_urls:
            payload = await self._fetch_metadata(
                metadata_url,
                raise_on_http_error=raise_on_http_error,
            )
            if payload is None:
                continue
            authorization_endpoint = optional_string(payload, "authorization_endpoint")
            token_endpoint = optional_string(payload, "token_endpoint")
            if authorization_endpoint is None or token_endpoint is None:
                continue
            return McpOAuthEndpoints(
                authorization_endpoint=authorization_endpoint,
                token_endpoint=token_endpoint,
                registration_endpoint=optional_string(
                    payload,
                    "registration_endpoint",
                ),
                resource_parameter_supported=bool(
                    payload.get("resource_parameter_supported"),
                ),
            )
        return McpOAuthEndpoints(
            authorization_endpoint="",
            token_endpoint="",
        )

    async def _fetch_metadata(
        self,
        metadata_url: str,
        *,
        raise_on_http_error: bool = False,
    ) -> dict[str, object] | None:
        try:
            await self._url_policy.validate_url(metadata_url)
            response = await self._http_client.get(
                metadata_url,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except (httpx.HTTPError, ValueError):
            return None
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            if raise_on_http_error:
                message = f"OAuth metadata discovery failed with {response.status_code}"
                raise McpOAuthConfigurationError(message)
            return None
        return object_mapping(response.content)

    async def _request_token(
        self,
        client_info: McpOAuthClientInfo,
        data: dict[str, str],
    ) -> McpOAuthToken:
        request_data = dict(data)
        basic_auth: tuple[str, str] | None = None
        if (
            client_info.client_secret is not None
            and client_info.token_endpoint_auth_method == McpTokenEndpointAuthMethod.CLIENT_SECRET_BASIC
        ):
            basic_auth = (client_info.client_id, client_info.client_secret)
        elif (
            client_info.client_secret is not None
            and client_info.token_endpoint_auth_method == McpTokenEndpointAuthMethod.CLIENT_SECRET_POST
        ):
            request_data["client_secret"] = client_info.client_secret

        if basic_auth is None:
            await self._url_policy.validate_url(client_info.token_endpoint)
            response = await self._http_client.post(
                client_info.token_endpoint,
                data=request_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        else:
            await self._url_policy.validate_url(client_info.token_endpoint)
            response = await self._http_client.post(
                client_info.token_endpoint,
                data=request_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                auth=basic_auth,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        if response.status_code >= 400:
            message = f"OAuth token request failed with {response.status_code}"
            raise McpOAuthConfigurationError(message)

        return token_from_payload(object_mapping(response.content))


def token_from_payload(payload: dict[str, object]) -> McpOAuthToken:
    access_token = required_string(payload, "access_token")
    token_type = optional_string(payload, "token_type") or "Bearer"
    refresh_token = optional_string(payload, "refresh_token")
    scope = optional_string(payload, "scope")
    expires_in = optional_number(payload, "expires_in")
    expires_at = None
    if expires_in is not None:
        expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
    return McpOAuthToken(
        access_token=access_token,
        token_type=token_type,
        refresh_token=refresh_token,
        scope=scope,
        expires_at=expires_at,
    )


def resolved_auth_method(
    requested_method: McpTokenEndpointAuthMethod | None,
    client_secret: SecretStr | None,
) -> McpTokenEndpointAuthMethod:
    if requested_method is not None:
        return requested_method
    if client_secret is None:
        return McpTokenEndpointAuthMethod.NONE
    return McpTokenEndpointAuthMethod.CLIENT_SECRET_BASIC


def add_resource_parameter(
    data: dict[str, str],
    client_info: McpOAuthClientInfo,
) -> None:
    if client_info.resource is not None:
        data["resource"] = client_info.resource


def provider_authorization_parameters(
    authorization_endpoint: str,
) -> dict[str, str]:
    if urlparse(authorization_endpoint).hostname == GOOGLE_AUTHORIZATION_HOST:
        return {
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
        }
    return {}


def pkce_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def state_hash(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def origin_from_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def has_authorization_endpoints(endpoints: McpOAuthEndpoints) -> bool:
    return bool(endpoints.authorization_endpoint and endpoints.token_endpoint)


def protected_resource_metadata_urls_for_resource(resource_url: str) -> tuple[str, ...]:
    parsed = urlparse(validated_mcp_outbound_url(resource_url))
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.strip("/")
    candidates: list[str] = []
    if path:
        candidates.append(urljoin(origin, f"{PROTECTED_RESOURCE_METADATA_PATH}/{path}"))
    candidates.append(urljoin(origin, PROTECTED_RESOURCE_METADATA_PATH))
    return tuple(dict.fromkeys(candidates))


def oauth_metadata_urls_for_issuer(issuer_url: str) -> tuple[str, ...]:
    validated_issuer_url = validated_mcp_outbound_url(issuer_url)
    parsed = urlparse(validated_issuer_url)
    if "/.well-known/" in parsed.path:
        return (validated_issuer_url,)

    origin = f"{parsed.scheme}://{parsed.netloc}"
    issuer_path = parsed.path.rstrip("/")
    candidates: list[str] = []
    if issuer_path:
        candidates.append(
            urlunparse(
                (
                    parsed.scheme,
                    parsed.netloc,
                    f"{OAUTH_METADATA_PATH}{issuer_path}",
                    "",
                    "",
                    "",
                ),
            ),
        )
    candidates.append(urljoin(origin, OAUTH_METADATA_PATH))
    if issuer_path:
        candidates.append(
            urlunparse(
                (
                    parsed.scheme,
                    parsed.netloc,
                    f"{OPENID_METADATA_PATH}{issuer_path}",
                    "",
                    "",
                    "",
                ),
            ),
        )
    candidates.append(urljoin(origin, OPENID_METADATA_PATH))
    return tuple(dict.fromkeys(candidates))


def resource_metadata_url_from_www_authenticate(header_value: str) -> str | None:
    match = RESOURCE_METADATA_PATTERN.search(header_value)
    if match is None:
        return None
    return match.group(1) or match.group(2)


def string_sequence(payload: dict[str, object], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list):
        return ()
    values: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            values.append(item.strip())
    return tuple(dict.fromkeys(values))


def scope_text(scopes: tuple[str, ...]) -> str:
    return " ".join(scope.strip() for scope in scopes if scope.strip())


def secret_value(secret: SecretStr | None) -> str | None:
    if secret is None:
        return None
    value = secret.get_secret_value().strip()
    return value or None


def object_mapping(content: bytes) -> dict[str, object]:
    payload = orjson.loads(content)
    if not isinstance(payload, dict):
        raise McpOAuthConfigurationError(JSON_OBJECT_RESPONSE_MESSAGE)
    return {key: value for key, value in payload.items() if isinstance(key, str)}


def required_string(payload: dict[str, object], key: str) -> str:
    value = optional_string(payload, key)
    if value is None:
        message = f"OAuth response is missing {key}"
        raise McpOAuthConfigurationError(message)
    return value


def optional_string(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        message = f"OAuth response field {key} must be a string"
        raise McpOAuthConfigurationError(message)
    stripped_value = value.strip()
    return stripped_value or None


def optional_number(payload: dict[str, object], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        message = f"OAuth response field {key} must be a number"
        raise McpOAuthConfigurationError(message)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    message = f"OAuth response field {key} must be a number"
    raise McpOAuthConfigurationError(message)
